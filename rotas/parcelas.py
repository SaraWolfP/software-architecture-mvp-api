"""
parcelas.py
Rotas de consulta do cronograma de parcelas.

As parcelas são um recurso somente-leitura: elas nascem do contrato e das
amortizações, e são regravadas automaticamente sempre que um dos dois muda.
Não há POST, PUT nem DELETE aqui de propósito.

    GET /financiamento/<id>/parcelas           cronograma, com filtro por ano
    GET /financiamento/<id>/parcelas/resumo    totais consolidados
"""

import banco_de_dados as bd
from flask import Blueprint, jsonify, request

bp = Blueprint('parcelas', __name__, url_prefix='/financiamento')


def _financiamento_existe(conn, id_financiamento: int) -> bool:
    """Informa se um financiamento está cadastrado."""
    return bool(bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento))


@bp.route('/<int:id_financiamento>/parcelas', methods=['GET'])
def listar_parcelas_por_financiamento(id_financiamento: int):
    """
    Lista as parcelas de um financiamento em ordem cronológica.
    ---
    tags:
      - Parcelas
    parameters:
      - { in: path,  name: id_financiamento, type: integer, required: true }
      - { in: query, name: ano,     type: integer, required: false, description: "Filtra por ano (ex: 2027)." }
      - { in: query, name: agrupar, type: string,  enum: [ano], required: false, description: "Consolida os totais por ano." }
    responses:
      200: { description: Lista de parcelas ou totais anuais. }
      404: { description: Financiamento não encontrado. }
    """
    conn = bd.conecta_db()

    if not _financiamento_existe(conn, id_financiamento):
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    registros = bd.obtem_dados(
        conn, 'Parcelas',
        coluna='financiamento_id', valor=id_financiamento, ordem='numero_parcela',
    )
    conn.close()

    parcelas = [dict(registro) for registro in registros]

    ano = request.args.get('ano')
    if ano:
        parcelas = [p for p in parcelas if p['data_parcela'].startswith(str(ano))]

    if request.args.get('agrupar') == 'ano':
        return jsonify(_agrupa_por_ano(parcelas)), 200

    return jsonify(parcelas), 200


@bp.route('/<int:id_financiamento>/parcelas/resumo', methods=['GET'])
def resumir_parcelas(id_financiamento: int):
    """
    Consolida os totais do cronograma de um financiamento.
    ---
    tags:
      - Parcelas
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
    responses:
      200: { description: Totais de juros, amortização e desembolso. }
      404: { description: Financiamento não encontrado. }
    """
    conn = bd.conecta_db()

    if not _financiamento_existe(conn, id_financiamento):
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    registros = bd.obtem_dados(
        conn, 'Parcelas',
        coluna='financiamento_id', valor=id_financiamento, ordem='numero_parcela',
    )
    amortizacoes = bd.obtem_dados(
        conn, 'AmortizacoesExtras',
        coluna='financiamento_id', valor=id_financiamento,
    )
    conn.close()

    if not registros:
        return jsonify({
            "total_parcelas": 0,
            "total_juros": 0,
            "total_amortizado": 0,
            "total_desembolso": 0,
            "total_amortizacoes_extras": 0,
            "primeira_parcela": None,
            "ultima_parcela": None,
        }), 200

    total_juros = sum(r['juros'] for r in registros)
    total_amortizado = sum(r['amortizacao'] for r in registros)
    total_parcelas_pagas = sum(r['valor_parcela'] for r in registros)
    total_extras = sum(a['valor_amortizado'] for a in amortizacoes)

    return jsonify({
        "total_parcelas": len(registros),
        "total_juros": round(total_juros, 2),
        "total_amortizado": round(total_amortizado, 2),
        "total_desembolso": round(total_parcelas_pagas + total_extras, 2),
        "total_amortizacoes_extras": round(total_extras, 2),
        "primeira_parcela": registros[0]['data_parcela'],
        "ultima_parcela": registros[-1]['data_parcela'],
        "maior_parcela": round(max(r['valor_parcela'] for r in registros), 2),
        "menor_parcela": round(min(r['valor_parcela'] for r in registros), 2),
    }), 200


def _agrupa_por_ano(parcelas: list[dict]) -> list[dict]:
    """
    Consolida um cronograma mensal em totais anuais.

    Argumentos:
        parcelas: lista de parcelas, cada uma com 'data_parcela' em 'YYYY-MM'.

    Retorna:
        Lista de dicionários com um registro por ano, em ordem crescente.
    """
    agrupado: dict[str, dict] = {}

    for parcela in parcelas:
        ano = parcela['data_parcela'][:4]
        acumulado = agrupado.setdefault(ano, {
            'ano': ano,
            'parcelas': 0,
            'total_pago': 0.0,
            'total_juros': 0.0,
            'total_amortizado': 0.0,
        })
        acumulado['parcelas'] += 1
        acumulado['total_pago'] += parcela['valor_parcela']
        acumulado['total_juros'] += parcela['juros']
        acumulado['total_amortizado'] += parcela['amortizacao']

    for acumulado in agrupado.values():
        acumulado['total_pago'] = round(acumulado['total_pago'], 2)
        acumulado['total_juros'] = round(acumulado['total_juros'], 2)
        acumulado['total_amortizado'] = round(acumulado['total_amortizado'], 2)

    return [agrupado[ano] for ano in sorted(agrupado)]
