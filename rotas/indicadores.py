"""
indicadores.py
Rotas que expõem os dados da componente externa — a API SGS do Banco Central.

A interface nunca fala com o BCB diretamente. Ela consome estas rotas, que
buscam a série, convertem unidades, normalizam a Selic para base mensal,
guardam o resultado em cache e devolvem um formato estável. Se o BCB estiver
fora do ar, o cache assume e o campo `origem` avisa a interface.

    GET  /indicadores/                    valores correntes de CDI, Selic e IPCA
    GET  /indicadores/<nome>/historico    série histórica de um indicador
    POST /indicadores/atualizar           força a releitura no BCB
"""

import banco_de_dados as bd
from flask import Blueprint, jsonify, request

from servicos import cache_indicadores
from servicos.cliente_bcb import SERIES, ErroBCB

bp = Blueprint('indicadores', __name__, url_prefix='/indicadores')

#: Limites para o parâmetro `ultimos` do histórico.
HISTORICO_MINIMO = 2
HISTORICO_MAXIMO = 120


@bp.route('/', methods=['GET'])
def listar_indicadores():
    """
    Retorna os indicadores econômicos correntes, vindos do Banco Central.
    ---
    tags:
      - Indicadores (API externa)
    parameters:
      - { in: query, name: ultimos, type: integer, required: false, default: 12, description: "Tamanho do histórico incluído em cada indicador." }
    responses:
      200:
        description: |
          Indicadores com valor corrente, taxa mensal equivalente e histórico.
          O campo `origem` indica se o dado veio do BCB ou do cache local.
      502: { description: O BCB está indisponível e não há nada em cache. }
    """
    try:
        ultimos = _le_ultimos()
    except ValueError as erro:
        return jsonify({"erro": str(erro)}), 400

    conn = bd.conecta_db()
    try:
        resultado = cache_indicadores.obtem_todos(conn, ultimos=ultimos)
    finally:
        conn.close()

    indisponiveis = [i for i in resultado['indicadores'] if 'erro' in i]
    if len(indisponiveis) == len(resultado['indicadores']):
        return jsonify({
            "erro": "Não foi possível obter os indicadores no Banco Central e "
                    "não há dados em cache local.",
            "detalhes": [i['erro'] for i in indisponiveis],
        }), 502

    return jsonify(resultado), 200


@bp.route('/<nome>/historico', methods=['GET'])
def obter_historico(nome: str):
    """
    Retorna a série histórica de um indicador.
    ---
    tags:
      - Indicadores (API externa)
    parameters:
      - { in: path,  name: nome,    type: string,  enum: [CDI, SELIC, IPCA], required: true }
      - { in: query, name: ultimos, type: integer, required: false, default: 12 }
    responses:
      200: { description: Série histórica já convertida para decimal. }
      400: { description: Indicador desconhecido ou parâmetro inválido. }
      502: { description: O BCB está indisponível e não há nada em cache. }
    """
    nome = nome.upper()

    if nome not in SERIES:
        return jsonify({
            "erro": f"Indicador '{nome}' não existe. Disponíveis: {', '.join(SERIES)}."
        }), 400

    try:
        ultimos = _le_ultimos()
    except ValueError as erro:
        return jsonify({"erro": str(erro)}), 400

    conn = bd.conecta_db()
    try:
        indicador = cache_indicadores.obtem_indicador(conn, nome, ultimos=ultimos)
    except ErroBCB as erro:
        return jsonify({
            "erro": f"Não foi possível obter a série {nome}: {erro}"
        }), 502
    finally:
        conn.close()

    return jsonify(indicador), 200


@bp.route('/atualizar', methods=['POST'])
def atualizar_indicadores():
    """
    Força a releitura das séries no Banco Central, ignorando o cache.
    ---
    tags:
      - Indicadores (API externa)
    responses:
      200: { description: Cache regravado com os dados mais recentes do BCB. }
      502: { description: O BCB está indisponível. }
    """
    conn = bd.conecta_db()
    try:
        resultado = cache_indicadores.obtem_todos(conn, forcar=True)
    finally:
        conn.close()

    # Só conta como atualizado o que veio de fato do BCB nesta chamada. Uma
    # série servida do cache vencido é uma falha para esta rota, cujo único
    # propósito é reler a fonte — reportá-la como sucesso seria mentir.
    atualizados = [i['nome'] for i in resultado['indicadores'] if i.get('origem') == 'bcb']
    falhas = [i['nome'] for i in resultado['indicadores'] if i.get('origem') != 'bcb']

    if not atualizados:
        return jsonify({
            "erro": "Nenhuma série pôde ser atualizada. O Banco Central parece indisponível.",
            "falhas": falhas,
        }), 502

    return jsonify({
        "atualizados": atualizados,
        "falhas": falhas,
        "origem": resultado['origem'],
        "mensagem": f"{len(atualizados)} série(s) atualizada(s) a partir do Banco Central.",
    }), 200


def _le_ultimos() -> int:
    """
    Lê e valida o parâmetro de consulta `ultimos`.

    Retorna:
        Quantidade de observações, dentro dos limites aceitos.

    Lança:
        ValueError: se o parâmetro não for um inteiro.
    """
    try:
        ultimos = int(request.args.get('ultimos', 12))
    except (TypeError, ValueError) as erro:
        raise ValueError("O parâmetro 'ultimos' deve ser um número inteiro.") from erro

    return max(HISTORICO_MINIMO, min(ultimos, HISTORICO_MAXIMO))
