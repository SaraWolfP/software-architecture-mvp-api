"""
amortizacoes.py
Rotas REST das amortizações extraordinárias.

Toda operação de escrita aqui dispara a reconstrução completa do cronograma do
financiamento, porque uma amortização altera o saldo devedor e, com ele, todas
as parcelas seguintes.

    POST   /financiamento/<id>/amortizacoes
    GET    /financiamento/<id>/amortizacoes
    PUT    /financiamento/<id>/amortizacoes/<id_amortizacao>
    DELETE /financiamento/<id>/amortizacoes/<id_amortizacao>
"""

from sqlite3 import IntegrityError

import banco_de_dados as bd
from flask import Blueprint, jsonify, request

from servicos.calculadora import TIPOS_AMORTIZACAO, AmortizacaoExtra
from servicos.cronograma import marcar_simulacoes_obsoletas, regravar_parcelas
from servicos.validacao import (
    ErroValidacao,
    exige_campos,
    exige_corpo,
    valida_competencia,
    valida_numero,
    valida_opcao,
)

bp = Blueprint('amortizacoes', __name__, url_prefix='/financiamento')


def _carrega_financiamento(conn, id_financiamento: int):
    """
    Busca um financiamento, devolvendo None se ele não existir.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        id_financiamento: identificador do contrato.

    Retorna:
        A linha do financiamento ou None.
    """
    resultado = bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento)
    return resultado[0] if resultado else None


def _valida_amortizacao(dados: dict, linha_financiamento) -> dict:
    """
    Valida o corpo de criação ou edição de uma amortização extraordinária.

    Argumentos:
        dados: corpo da requisição já verificado por `exige_corpo`.
        linha_financiamento: contrato ao qual a amortização pertence.

    Retorna:
        Dicionário pronto para persistência.

    Lança:
        ErroValidacao: se algum campo for inválido ou a data cair fora do contrato.
    """
    exige_campos(dados, ['valor_amortizado', 'data_amortizacao', 'tipo'])

    valor = valida_numero(dados['valor_amortizado'], 'valor_amortizado', minimo=0)
    data = valida_competencia(dados['data_amortizacao'], 'data_amortizacao')
    tipo = valida_opcao(dados['tipo'], 'tipo', TIPOS_AMORTIZACAO)

    if data < linha_financiamento['data_inicio']:
        raise ErroValidacao(
            "A amortização não pode ser anterior ao início do financiamento "
            f"({linha_financiamento['data_inicio']})."
        )

    return AmortizacaoExtra(
        valor_amortizado=valor,
        data_amortizacao=data,
        tipo=tipo,
    ).to_dict()


@bp.route('/<int:id_financiamento>/amortizacoes', methods=['POST'])
def criar_amortizacao_extra(id_financiamento: int):
    """
    Registra uma amortização extraordinária e recalcula o cronograma.
    ---
    tags:
      - Amortizações Extras
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [valor_amortizado, data_amortizacao, tipo]
          properties:
            valor_amortizado: { type: number, example: 50000 }
            data_amortizacao: { type: string, example: "2027-06" }
            tipo:
              type: string
              enum: [PARCELA, PRAZO]
              example: PARCELA
              description: "PARCELA mantém o prazo e reduz o valor; PRAZO mantém o valor e antecipa a quitação."
    responses:
      201: { description: Amortização registrada e parcelas recalculadas. }
      400: { description: Dados inválidos ou ausentes. }
      404: { description: Financiamento não encontrado. }
      409: { description: Já existe uma amortização nesta competência. }
    """
    conn = bd.conecta_db()
    financiamento = _carrega_financiamento(conn, id_financiamento)

    if financiamento is None:
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    try:
        amortizacao = _valida_amortizacao(
            exige_corpo(request.get_json(silent=True)), financiamento
        )
    except ErroValidacao as erro:
        conn.close()
        return jsonify({"erro": str(erro)}), 400

    amortizacao['financiamento_id'] = id_financiamento

    try:
        id_amortizacao = bd.insere_dado(conn, 'AmortizacoesExtras', amortizacao)
    except IntegrityError:
        conn.close()
        return jsonify({
            "erro": "Já existe uma amortização para este financiamento nesta competência."
        }), 409

    total_parcelas = regravar_parcelas(conn, id_financiamento, financiamento)
    marcar_simulacoes_obsoletas(conn, id_financiamento)
    conn.close()

    return jsonify({
        "id": id_amortizacao,
        "parcelas_geradas": total_parcelas,
        "mensagem": "Amortização registrada e parcelas recalculadas.",
    }), 201


@bp.route('/<int:id_financiamento>/amortizacoes', methods=['GET'])
def listar_amortizacoes_por_financiamento(id_financiamento: int):
    """
    Lista as amortizações extraordinárias de um financiamento.
    ---
    tags:
      - Amortizações Extras
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - { in: query, name: tipo, type: string, enum: [PARCELA, PRAZO], required: false }
    responses:
      200: { description: Lista de amortizações em ordem cronológica. }
      404: { description: Financiamento não encontrado. }
    """
    conn = bd.conecta_db()

    if _carrega_financiamento(conn, id_financiamento) is None:
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    registros = bd.obtem_dados(
        conn, 'AmortizacoesExtras',
        coluna='financiamento_id', valor=id_financiamento, ordem='data_amortizacao',
    )
    conn.close()

    filtro_tipo = request.args.get('tipo')
    amortizacoes = [dict(registro) for registro in registros]

    if filtro_tipo:
        filtro_tipo = filtro_tipo.upper()
        amortizacoes = [a for a in amortizacoes if a['tipo'] == filtro_tipo]

    return jsonify(amortizacoes), 200


@bp.route('/<int:id_financiamento>/amortizacoes/<int:id_amortizacao>', methods=['PUT'])
def atualizar_amortizacao_extra(id_financiamento: int, id_amortizacao: int):
    """
    Altera uma amortização extraordinária e reconstrói o cronograma.
    ---
    tags:
      - Amortizações Extras
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - { in: path, name: id_amortizacao,   type: integer, required: true }
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [valor_amortizado, data_amortizacao, tipo]
          properties:
            valor_amortizado: { type: number, example: 75000 }
            data_amortizacao: { type: string, example: "2027-08" }
            tipo:             { type: string, enum: [PARCELA, PRAZO], example: PRAZO }
    responses:
      200: { description: Amortização atualizada e parcelas recalculadas. }
      400: { description: Dados inválidos ou ausentes. }
      404: { description: Amortização ou financiamento não encontrado. }
      409: { description: Já existe outra amortização nesta competência. }
    """
    conn = bd.conecta_db()
    financiamento = _carrega_financiamento(conn, id_financiamento)

    if financiamento is None:
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    existente = bd.obtem_dados(conn, 'AmortizacoesExtras', coluna='id', valor=id_amortizacao)
    if not existente or existente[0]['financiamento_id'] != id_financiamento:
        conn.close()
        return jsonify({"erro": "Amortização não encontrada neste financiamento."}), 404

    try:
        amortizacao = _valida_amortizacao(
            exige_corpo(request.get_json(silent=True)), financiamento
        )
    except ErroValidacao as erro:
        conn.close()
        return jsonify({"erro": str(erro)}), 400

    try:
        bd.atualiza_dado(conn, 'AmortizacoesExtras', amortizacao, valor=id_amortizacao)
    except IntegrityError:
        conn.close()
        return jsonify({
            "erro": "Já existe outra amortização para este financiamento nesta competência."
        }), 409

    total_parcelas = regravar_parcelas(conn, id_financiamento, financiamento)
    marcar_simulacoes_obsoletas(conn, id_financiamento)
    conn.close()

    return jsonify({
        "id": id_amortizacao,
        "parcelas_geradas": total_parcelas,
        "mensagem": "Amortização atualizada e parcelas recalculadas.",
    }), 200


@bp.route('/<int:id_financiamento>/amortizacoes/<int:id_amortizacao>', methods=['DELETE'])
def deletar_amortizacao_extra(id_financiamento: int, id_amortizacao: int):
    """
    Remove uma amortização extraordinária e reconstrói o cronograma.
    ---
    tags:
      - Amortizações Extras
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - { in: path, name: id_amortizacao,   type: integer, required: true }
    responses:
      200: { description: Amortização removida e parcelas recalculadas. }
      404: { description: Amortização ou financiamento não encontrado. }
    """
    conn = bd.conecta_db()
    financiamento = _carrega_financiamento(conn, id_financiamento)

    if financiamento is None:
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    existente = bd.obtem_dados(conn, 'AmortizacoesExtras', coluna='id', valor=id_amortizacao)
    if not existente or existente[0]['financiamento_id'] != id_financiamento:
        conn.close()
        return jsonify({"erro": "Amortização não encontrada neste financiamento."}), 404

    bd.deleta_dados(conn, 'AmortizacoesExtras', valor=id_amortizacao)

    total_parcelas = regravar_parcelas(conn, id_financiamento, financiamento)
    marcar_simulacoes_obsoletas(conn, id_financiamento)
    conn.close()

    return jsonify({
        "parcelas_geradas": total_parcelas,
        "mensagem": "Amortização removida e parcelas recalculadas.",
    }), 200
