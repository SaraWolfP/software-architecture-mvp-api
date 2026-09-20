"""
financiamento.py
Rotas REST do recurso Financiamento — o CRUD completo do contrato.

Cobre os quatro métodos HTTP:
    POST   /financiamento/        cria e calcula o cronograma
    GET    /financiamento/        lista com filtro, ordenação e paginação
    GET    /financiamento/<id>    detalha um contrato
    PUT    /financiamento/<id>    edita e recalcula o cronograma
    DELETE /financiamento/<id>    remove em cascata
"""

from sqlite3 import IntegrityError

import banco_de_dados as bd
from flask import Blueprint, jsonify, request

from servicos.calculadora import MODELOS_SUPORTADOS, Financiamento
from servicos.cronograma import marcar_simulacoes_obsoletas, regravar_parcelas
from servicos.validacao import (
    ErroValidacao,
    exige_campos,
    exige_corpo,
    valida_competencia,
    valida_nome,
    valida_numero,
    valida_opcao,
)

bp = Blueprint('financiamento', __name__, url_prefix='/financiamento')

#: Colunas pelas quais a listagem pode ser ordenada.
#:
#: Esta allowlist não é cosmética: `busca_paginada` interpola o nome da coluna
#: diretamente na string SQL, então aceitar um valor arbitrário do usuário aqui
#: abriria uma injeção de SQL.
COLUNAS_ORDENAVEIS = (
    'id', 'nome', 'valor_imovel', 'entrada',
    'taxa_juros', 'prazo_meses', 'data_inicio', 'modelo',
)

#: Campos aceitos como filtro de igualdade na listagem.
FILTROS_PERMITIDOS = ('modelo',)

#: Teto de itens por página, para evitar que um cliente peça a base inteira.
POR_PAGINA_MAXIMO = 100

#: Campos do contrato, com o validador de cada um.
CAMPOS_CONTRATO = ('nome', 'valor_imovel', 'entrada', 'taxa_juros',
                   'prazo_meses', 'data_inicio', 'modelo')


def _valida_contrato(dados: dict) -> dict:
    """
    Valida e normaliza o corpo de criação ou edição de um financiamento.

    A taxa chega da interface em percentual mensal (0,89 significa 0,89% a.m.)
    e é persistida em decimal (0,0089), que é a unidade usada pela calculadora.

    Argumentos:
        dados: corpo da requisição já verificado por `exige_corpo`.

    Retorna:
        Dicionário pronto para persistência.

    Lança:
        ErroValidacao: na primeira inconsistência encontrada.
    """
    exige_campos(dados, list(CAMPOS_CONTRATO))

    nome = valida_nome(dados['nome'])
    valor_imovel = valida_numero(dados['valor_imovel'], 'valor_imovel', minimo=0)
    entrada = valida_numero(dados['entrada'], 'entrada', minimo=-0.01)
    taxa_juros = valida_numero(dados['taxa_juros'], 'taxa_juros', minimo=0, maximo=100)
    prazo_meses = valida_numero(dados['prazo_meses'], 'prazo_meses', minimo=0, inteiro=True)
    data_inicio = valida_competencia(dados['data_inicio'], 'data_inicio')
    modelo = valida_opcao(dados['modelo'], 'modelo', MODELOS_SUPORTADOS)

    if entrada >= valor_imovel:
        raise ErroValidacao("A entrada deve ser menor que o valor do imóvel.")

    if prazo_meses > 600:
        raise ErroValidacao("O prazo deve ser de no máximo 600 meses (50 anos).")

    return Financiamento(
        nome=nome,
        valor_imovel=valor_imovel,
        entrada=entrada,
        taxa_juros=taxa_juros / 100,
        prazo_meses=prazo_meses,
        data_inicio=data_inicio,
        modelo=modelo,
    ).to_dict()


@bp.route('/', methods=['POST'])
def criar_financiamento():
    """
    Cria um financiamento e calcula todo o seu cronograma de parcelas.
    ---
    tags:
      - Financiamentos
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [nome, valor_imovel, entrada, taxa_juros, prazo_meses, data_inicio, modelo]
          properties:
            nome:         { type: string,  example: "Apartamento Botafogo" }
            valor_imovel: { type: number,  example: 500000 }
            entrada:      { type: number,  example: 100000 }
            taxa_juros:   { type: number,  example: 0.89, description: "Percentual mensal (0.89 = 0,89% a.m.)" }
            prazo_meses:  { type: integer, example: 360 }
            data_inicio:  { type: string,  example: "2026-01" }
            modelo:       { type: string,  enum: [SAC, PRICE], example: SAC }
    responses:
      201: { description: Financiamento criado e cronograma calculado. }
      400: { description: Dados inválidos ou ausentes. }
      409: { description: Já existe um financiamento com esse nome. }
    """
    try:
        contrato = _valida_contrato(exige_corpo(request.get_json(silent=True)))
    except ErroValidacao as erro:
        return jsonify({"erro": str(erro)}), 400

    conn = bd.conecta_db()
    try:
        id_financiamento = bd.insere_dado(conn, 'Financiamentos', contrato)
    except IntegrityError:
        conn.close()
        return jsonify({"erro": f"Já existe um financiamento chamado '{contrato['nome']}'."}), 409

    total_parcelas = regravar_parcelas(conn, id_financiamento)
    conn.close()

    return jsonify({
        "id": id_financiamento,
        "parcelas_geradas": total_parcelas,
        "mensagem": "Financiamento criado com sucesso.",
    }), 201


@bp.route('/', methods=['GET'])
def listar_financiamentos():
    """
    Lista financiamentos com filtro, ordenação e paginação.
    ---
    tags:
      - Financiamentos
    parameters:
      - { in: query, name: modelo,      type: string,  enum: [SAC, PRICE], required: false }
      - { in: query, name: ordenar_por, type: string,  required: false, default: id }
      - { in: query, name: ordem,       type: string,  enum: [asc, desc], required: false, default: asc }
      - { in: query, name: pagina,      type: integer, required: false, default: 1 }
      - { in: query, name: por_pagina,  type: integer, required: false, default: 10 }
    responses:
      200: { description: Página de financiamentos com metadados de paginação. }
      400: { description: Parâmetro de consulta inválido. }
    """
    ordenar_por = request.args.get('ordenar_por', 'id')
    ordem = request.args.get('ordem', 'asc').lower()

    if ordenar_por not in COLUNAS_ORDENAVEIS:
        return jsonify({
            "erro": f"Não é possível ordenar por '{ordenar_por}'. "
                    f"Use um de: {', '.join(COLUNAS_ORDENAVEIS)}."
        }), 400

    if ordem not in ('asc', 'desc'):
        return jsonify({"erro": "O parâmetro 'ordem' deve ser 'asc' ou 'desc'."}), 400

    try:
        pagina = max(int(request.args.get('pagina', 1)), 1)
        por_pagina = int(request.args.get('por_pagina', 10))
    except (TypeError, ValueError):
        return jsonify({"erro": "Os parâmetros 'pagina' e 'por_pagina' devem ser inteiros."}), 400

    por_pagina = max(1, min(por_pagina, POR_PAGINA_MAXIMO))

    filtros = {}
    for campo in FILTROS_PERMITIDOS:
        valor = request.args.get(campo)
        if valor:
            filtros[campo] = valor.upper()

    conn = bd.conecta_db()
    total = bd.conta_dados(conn, 'Financiamentos', filtros)
    registros = bd.busca_paginada(
        conn, 'Financiamentos',
        filtros=filtros, ordenar_por=ordenar_por, ordem=ordem,
        limite=por_pagina, deslocamento=(pagina - 1) * por_pagina,
    )
    conn.close()

    total_paginas = (total + por_pagina - 1) // por_pagina

    return jsonify({
        "itens": [dict(registro) for registro in registros],
        "paginacao": {
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total": total,
            "total_paginas": total_paginas,
        },
    }), 200


@bp.route('/<int:id_financiamento>', methods=['GET'])
def obter_financiamento(id_financiamento: int):
    """
    Retorna um financiamento pelo identificador.
    ---
    tags:
      - Financiamentos
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
    responses:
      200: { description: Financiamento encontrado. }
      404: { description: Financiamento não encontrado. }
    """
    conn = bd.conecta_db()
    resultado = bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento)
    conn.close()

    if not resultado:
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    return jsonify(dict(resultado[0])), 200


@bp.route('/<int:id_financiamento>', methods=['PUT'])
def atualizar_financiamento(id_financiamento: int):
    """
    Atualiza um financiamento e reconstrói todo o cronograma.
    ---
    tags:
      - Financiamentos
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [nome, valor_imovel, entrada, taxa_juros, prazo_meses, data_inicio, modelo]
          properties:
            nome:         { type: string,  example: "Apartamento Botafogo" }
            valor_imovel: { type: number,  example: 520000 }
            entrada:      { type: number,  example: 120000 }
            taxa_juros:   { type: number,  example: 0.82 }
            prazo_meses:  { type: integer, example: 300 }
            data_inicio:  { type: string,  example: "2026-01" }
            modelo:       { type: string,  enum: [SAC, PRICE], example: PRICE }
    responses:
      200: { description: Financiamento atualizado e cronograma recalculado. }
      400: { description: Dados inválidos ou ausentes. }
      404: { description: Financiamento não encontrado. }
      409: { description: Já existe outro financiamento com esse nome. }
    """
    try:
        contrato = _valida_contrato(exige_corpo(request.get_json(silent=True)))
    except ErroValidacao as erro:
        return jsonify({"erro": str(erro)}), 400

    conn = bd.conecta_db()

    if not bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento):
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    try:
        bd.atualiza_dado(conn, 'Financiamentos', contrato, valor=id_financiamento)
    except IntegrityError:
        conn.close()
        return jsonify({
            "erro": f"Já existe outro financiamento chamado '{contrato['nome']}'."
        }), 409

    # Amortizações fora do novo prazo não têm mais onde ser aplicadas.
    amortizacoes = bd.obtem_dados(
        conn, 'AmortizacoesExtras',
        coluna='financiamento_id', valor=id_financiamento,
    )
    removidas = 0
    total_parcelas = regravar_parcelas(conn, id_financiamento)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT MAX(data_parcela) AS fim FROM Parcelas WHERE financiamento_id = ?",
        (id_financiamento,),
    )
    ultima = cursor.fetchone()['fim']

    if ultima:
        for amortizacao in amortizacoes:
            fora_do_prazo = (
                amortizacao['data_amortizacao'] > ultima
                or amortizacao['data_amortizacao'] < contrato['data_inicio']
            )
            if fora_do_prazo:
                bd.deleta_dados(conn, 'AmortizacoesExtras', valor=amortizacao['id'])
                removidas += 1

        if removidas:
            total_parcelas = regravar_parcelas(conn, id_financiamento)

    # As simulações guardadas foram calculadas sobre o cronograma antigo.
    obsoletas = marcar_simulacoes_obsoletas(conn, id_financiamento)
    conn.close()

    return jsonify({
        "id": id_financiamento,
        "parcelas_geradas": total_parcelas,
        "amortizacoes_removidas": removidas,
        "simulacoes_obsoletas": obsoletas,
        "mensagem": "Financiamento atualizado e cronograma recalculado.",
    }), 200


@bp.route('/<int:id_financiamento>', methods=['DELETE'])
def deletar_financiamento(id_financiamento: int):
    """
    Deleta um financiamento e tudo que depende dele.
    ---
    tags:
      - Financiamentos
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
    responses:
      200: { description: Financiamento deletado com sucesso. }
      404: { description: Financiamento não encontrado. }
    """
    conn = bd.conecta_db()
    linhas_afetadas = bd.deleta_dados(conn, 'Financiamentos', valor=id_financiamento)
    conn.close()

    if linhas_afetadas == 0:
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    return jsonify({"mensagem": "Financiamento deletado com sucesso."}), 200
