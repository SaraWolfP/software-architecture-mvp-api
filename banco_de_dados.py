"""
banco_de_dados.py
Camada de acesso ao SQLite: conexão, DDL e operações genéricas de CRUD.

O caminho do arquivo do banco vem da variável de ambiente DB_PATH, o que permite
apontá-lo para um volume Docker sem alterar o código. O modo WAL e o timeout de
escrita evitam o erro "database is locked" quando há concorrência entre threads.
"""

from __future__ import annotations

import os
import sqlite3

#: Caminho do arquivo do banco. Em container é sobrescrito para /app/dados/.
DB_PATH = os.getenv('DB_PATH', 'banco_de_dados.db')

#: Segundos que uma escrita aguarda antes de desistir com "database is locked".
DB_TIMEOUT = float(os.getenv('DB_TIMEOUT', '10'))


def conecta_db(db_caminho: str | None = None) -> sqlite3.Connection:
    """
    Cria uma conexão com o banco de dados SQLite.

    Argumentos:
        db_caminho: caminho para o arquivo do banco. Se omitido, usa DB_PATH.

    Retorna:
        conn: conexão com chaves estrangeiras, WAL e row_factory ativados.
    """
    caminho = db_caminho or DB_PATH

    diretorio = os.path.dirname(caminho)
    if diretorio:
        os.makedirs(diretorio, exist_ok=True)

    conn = sqlite3.connect(caminho, timeout=DB_TIMEOUT)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn


def cria_tabela(conn: sqlite3.Connection, nome_tabela: str, colunas: list[str]) -> None:
    """
    Cria uma tabela no banco de dados SQLite caso ela ainda não exista.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela a ser criada.
        colunas: lista de strings com as definições de cada coluna.
    """
    cursor = conn.cursor()
    cursor.execute(f"CREATE TABLE IF NOT EXISTS {nome_tabela} ({', '.join(colunas)})")
    conn.commit()


def insere_dado(conn: sqlite3.Connection, nome_tabela: str, dados: dict) -> int:
    """
    Insere um registro em uma tabela do banco de dados.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de destino.
        dados: dicionário com os valores a inserir (chave = nome da coluna).

    Retorna:
        id gerado automaticamente pelo banco para o registro inserido (lastrowid).
    """
    cursor = conn.cursor()
    colunas = ', '.join(dados.keys())
    valores = ', '.join(['?' for _ in dados])
    cursor.execute(
        f"INSERT INTO {nome_tabela} ({colunas}) VALUES ({valores})",
        list(dados.values())
    )
    conn.commit()
    return cursor.lastrowid


def insere_ou_substitui(conn: sqlite3.Connection, nome_tabela: str, dados: dict) -> int:
    """
    Insere um registro, substituindo-o caso viole uma constraint UNIQUE.

    Usado pelo cache de indicadores, em que a chave lógica é
    (codigo_serie, data_referencia) e o valor mais recente do BCB deve
    sobrescrever o anterior.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de destino.
        dados: dicionário com os valores a gravar.

    Retorna:
        id do registro gravado.
    """
    cursor = conn.cursor()
    colunas = ', '.join(dados.keys())
    valores = ', '.join(['?' for _ in dados])
    cursor.execute(
        f"INSERT OR REPLACE INTO {nome_tabela} ({colunas}) VALUES ({valores})",
        list(dados.values())
    )
    conn.commit()
    return cursor.lastrowid


def obtem_dados(
    conn: sqlite3.Connection,
    nome_tabela: str,
    coluna: str | None = None,
    valor=None,
    ordem: str | None = None,
) -> list:
    """
    Obtém registros de uma tabela com filtragem e ordenação opcionais.

    Sempre retorna uma lista. Para verificar se um registro único existe,
    cheque se a lista está vazia ou acesse o índice [0].

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de origem.
        coluna: coluna usada no filtro WHERE (opcional).
        valor: valor comparado na coluna do filtro (obrigatório se coluna for informada).
        ordem: coluna usada para ordenar os resultados (opcional).

    Retorna:
        Lista de registros que satisfazem o filtro (pode ser vazia).
    """
    query = f"SELECT * FROM {nome_tabela}"
    params: tuple = ()

    if coluna is not None:
        query += f" WHERE {coluna} = ?"
        params = (valor,)

    if ordem:
        query += f" ORDER BY {ordem}"

    cursor = conn.cursor()
    cursor.execute(query, params)

    return cursor.fetchall()


def conta_dados(
    conn: sqlite3.Connection,
    nome_tabela: str,
    filtros: dict | None = None,
) -> int:
    """
    Conta quantos registros satisfazem um conjunto de filtros de igualdade.

    Usado pela paginação para calcular o total de páginas sem trazer os
    registros para a memória.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de origem.
        filtros: dicionário coluna -> valor. As chaves devem vir de uma
            allowlist definida pelo chamador, nunca diretamente do usuário.

    Retorna:
        Quantidade de registros.
    """
    query = f"SELECT COUNT(*) AS total FROM {nome_tabela}"
    params: list = []

    if filtros:
        condicoes = ' AND '.join(f"{coluna} = ?" for coluna in filtros)
        query += f" WHERE {condicoes}"
        params = list(filtros.values())

    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor.fetchone()['total']


def busca_paginada(
    conn: sqlite3.Connection,
    nome_tabela: str,
    filtros: dict | None = None,
    ordenar_por: str = 'id',
    ordem: str = 'asc',
    limite: int = 10,
    deslocamento: int = 0,
) -> list:
    """
    Busca registros com filtros de igualdade, ordenação e paginação.

    Atenção de segurança: nome_tabela, as chaves de filtros, ordenar_por e ordem
    são interpolados na string SQL e NÃO podem vir diretamente do usuário. As
    rotas que usam esta função validam esses nomes contra uma allowlist antes
    de chamá-la. Apenas os valores são passados como parâmetros ligados.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de origem.
        filtros: dicionário coluna -> valor para o WHERE.
        ordenar_por: coluna de ordenação (já validada pelo chamador).
        ordem: 'asc' ou 'desc' (já validada pelo chamador).
        limite: quantidade máxima de registros.
        deslocamento: quantos registros pular (OFFSET).

    Retorna:
        Lista de registros da página solicitada.
    """
    direcao = 'DESC' if str(ordem).lower() == 'desc' else 'ASC'

    query = f"SELECT * FROM {nome_tabela}"
    params: list = []

    if filtros:
        condicoes = ' AND '.join(f"{coluna} = ?" for coluna in filtros)
        query += f" WHERE {condicoes}"
        params = list(filtros.values())

    query += f" ORDER BY {ordenar_por} {direcao} LIMIT ? OFFSET ?"
    params.extend([limite, deslocamento])

    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor.fetchall()


def atualiza_dado(
    conn: sqlite3.Connection,
    nome_tabela: str,
    dados: dict,
    coluna: str = 'id',
    valor=None,
) -> int:
    """
    Atualiza um ou mais registros de uma tabela.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de destino.
        dados: dicionário com as colunas a atualizar e seus novos valores.
        coluna: coluna usada no filtro WHERE (padrão: 'id').
        valor: valor comparado na coluna do filtro.

    Retorna:
        Número de registros atualizados.

    Exemplos:
        atualiza_dado(conn, 'Financiamentos', {'nome': 'Novo'}, valor=1)
            → renomeia o financiamento de id=1
    """
    if not dados:
        return 0

    atribuicoes = ', '.join(f"{chave} = ?" for chave in dados)
    params = list(dados.values()) + [valor]

    cursor = conn.cursor()
    cursor.execute(f"UPDATE {nome_tabela} SET {atribuicoes} WHERE {coluna} = ?", params)
    conn.commit()
    return cursor.rowcount


def deleta_dados(conn: sqlite3.Connection, nome_tabela: str, coluna: str = 'id', valor=None) -> int:
    """
    Deleta registros de uma tabela filtrados por coluna.

    Por padrão filtra pelo id primário. Para deletar por outra coluna,
    informe coluna e valor explicitamente.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        nome_tabela: nome da tabela de destino.
        coluna: coluna usada no filtro WHERE (padrão: 'id').
        valor: valor comparado na coluna do filtro.

    Retorna:
        Número de registros deletados.

    Exemplos:
        deleta_dados(conn, 'Financiamentos', valor=1)
            → deleta o financiamento com id=1

        deleta_dados(conn, 'Parcelas', coluna='financiamento_id', valor=1)
            → deleta todas as parcelas do financiamento 1
    """
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {nome_tabela} WHERE {coluna} = ?", (valor,))
    conn.commit()
    return cursor.rowcount


def inicializa_db() -> None:
    """
    Inicializa o banco de dados SQLite criando as cinco tabelas do sistema.

    Tabelas criadas:
        Financiamentos:     dados principais de cada empréstimo.
        Parcelas:           parcelas calculadas por financiamento (cascade delete).
        AmortizacoesExtras: amortizações extras por financiamento (cascade delete).
        IndicadoresCache:   cache local das séries do Banco Central.
        Simulacoes:         comparações "amortizar vs. investir" (cascade delete).

    É idempotente: pode ser chamada a cada import sem efeito colateral.
    """
    conn = conecta_db()

    cria_tabela(conn, 'Financiamentos', [
        'id                 INTEGER  PRIMARY KEY AUTOINCREMENT',
        'nome               TEXT     NOT NULL UNIQUE',
        'valor_imovel       NUMERIC  NOT NULL',
        'entrada            NUMERIC  NOT NULL',
        'taxa_juros         NUMERIC  NOT NULL',
        'prazo_meses        INTEGER  NOT NULL',
        'data_inicio        TEXT     NOT NULL',
        'modelo             TEXT     NOT NULL CHECK(modelo IN ("SAC", "PRICE"))'
    ])

    cria_tabela(conn, 'Parcelas', [
        'id               INTEGER  PRIMARY KEY AUTOINCREMENT',
        'financiamento_id INTEGER  NOT NULL REFERENCES Financiamentos(id) ON DELETE CASCADE',
        'numero_parcela   INTEGER  NOT NULL',
        'data_parcela     TEXT     NOT NULL',
        'valor_parcela    NUMERIC  NOT NULL',
        'juros            NUMERIC  NOT NULL DEFAULT 0',
        'amortizacao      NUMERIC  NOT NULL DEFAULT 0',
        'saldo_devedor    NUMERIC  NOT NULL DEFAULT 0',
    ])

    cria_tabela(conn, 'AmortizacoesExtras', [
        'id               INTEGER  PRIMARY KEY AUTOINCREMENT',
        'financiamento_id INTEGER  NOT NULL REFERENCES Financiamentos(id) ON DELETE CASCADE',
        'valor_amortizado NUMERIC  NOT NULL',
        'data_amortizacao TEXT     NOT NULL',
        'tipo             TEXT     NOT NULL CHECK(tipo IN ("PARCELA", "PRAZO"))',
        'UNIQUE(financiamento_id, data_amortizacao)',
    ])

    cria_tabela(conn, 'IndicadoresCache', [
        'id              INTEGER  PRIMARY KEY AUTOINCREMENT',
        'codigo_serie    INTEGER  NOT NULL',
        'nome_indicador  TEXT     NOT NULL',
        'data_referencia TEXT     NOT NULL',
        'valor           NUMERIC  NOT NULL',
        'atualizado_em   TEXT     NOT NULL',
        'UNIQUE(codigo_serie, data_referencia)',
    ])

    cria_tabela(conn, 'Simulacoes', [
        'id                   INTEGER  PRIMARY KEY AUTOINCREMENT',
        'financiamento_id     INTEGER  NOT NULL REFERENCES Financiamentos(id) ON DELETE CASCADE',
        'valor_aporte         NUMERIC  NOT NULL',
        'data_aporte          TEXT     NOT NULL',
        'indicador            TEXT     NOT NULL CHECK(indicador IN ("CDI", "SELIC"))',
        'percentual_indicador NUMERIC  NOT NULL DEFAULT 1.0',
        'taxa_mensal          NUMERIC  NOT NULL',
        'aliquota_ir          NUMERIC  NOT NULL',
        'meses_restantes      INTEGER  NOT NULL',
        'montante_amortizar   NUMERIC  NOT NULL',
        'montante_investir    NUMERIC  NOT NULL',
        'juros_evitados       NUMERIC  NOT NULL',
        'diferenca            NUMERIC  NOT NULL',
        'veredito             TEXT     NOT NULL CHECK(veredito IN ("AMORTIZAR", "INVESTIR", "EMPATE"))',
        'obsoleta             INTEGER  NOT NULL DEFAULT 0',
        'criada_em            TEXT     NOT NULL',
    ])

    conn.close()
