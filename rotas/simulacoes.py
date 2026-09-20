"""
simulacoes.py
Rotas da comparação "amortizar ou investir?" — a funcionalidade que amarra o
contrato local aos dados de mercado vindos da componente externa.

Cada simulação congela a taxa usada no momento do cálculo. Sem isso, uma
simulação salva hoje mostraria números irreproduzíveis amanhã, quando o CDI
tivesse mudado.

    POST   /financiamento/<id>/simulacoes
    GET    /financiamento/<id>/simulacoes
    PUT    /financiamento/<id>/simulacoes/<id_simulacao>
    DELETE /financiamento/<id>/simulacoes/<id_simulacao>
"""

from datetime import datetime, timezone

import banco_de_dados as bd
from flask import Blueprint, jsonify, request

from servicos import cache_indicadores
from servicos.calculadora import (
    TIPOS_AMORTIZACAO,
    amortizacoes_de_linhas,
    financiamento_de_linha,
)
from servicos.cliente_bcb import ErroBCB
from servicos.comparador import ErroSimulacao, compara_estrategias
from servicos.validacao import (
    ErroValidacao,
    exige_campos,
    exige_corpo,
    valida_competencia,
    valida_numero,
    valida_opcao,
)

bp = Blueprint('simulacoes', __name__, url_prefix='/financiamento')

#: Indicadores que podem lastrear uma simulação de investimento.
INDICADORES_ACEITOS = ('CDI', 'SELIC')

#: Colunas persistidas de uma simulação, na ordem em que aparecem na resposta.
CAMPOS_PERSISTIDOS = (
    'valor_aporte', 'data_aporte', 'indicador', 'percentual_indicador',
    'taxa_mensal', 'aliquota_ir', 'meses_restantes', 'montante_amortizar',
    'montante_investir', 'juros_evitados', 'diferenca', 'veredito',
)


def _valida_pedido(dados: dict) -> dict:
    """
    Valida o corpo de uma simulação.

    Argumentos:
        dados: corpo da requisição já verificado por `exige_corpo`.

    Retorna:
        Dicionário com os parâmetros normalizados.

    Lança:
        ErroValidacao: se algum campo estiver ausente ou fora do intervalo.
    """
    exige_campos(dados, ['valor_aporte', 'data_aporte'])

    return {
        'valor_aporte': valida_numero(dados['valor_aporte'], 'valor_aporte', minimo=0),
        'data_aporte': valida_competencia(dados['data_aporte'], 'data_aporte'),
        'indicador': valida_opcao(
            dados.get('indicador', 'CDI'), 'indicador', INDICADORES_ACEITOS
        ),
        'percentual_indicador': valida_numero(
            dados.get('percentual_indicador', 100), 'percentual_indicador',
            minimo=0, maximo=500,
        ) / 100,
        'tipo_amortizacao': valida_opcao(
            dados.get('tipo_amortizacao', 'PARCELA'), 'tipo_amortizacao', TIPOS_AMORTIZACAO
        ),
    }


def _executa_simulacao(conn, id_financiamento: int, pedido: dict) -> dict:
    """
    Roda a comparação para um financiamento, buscando a taxa do indicador.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        id_financiamento: contrato a simular.
        pedido: parâmetros já validados por `_valida_pedido`.

    Retorna:
        Resultado completo da comparação, acrescido do indicador usado.

    Lança:
        LookupError: se o financiamento não existir.
        ErroBCB: se não houver taxa disponível nem no BCB nem em cache.
        ErroSimulacao: se os parâmetros forem inconsistentes com o contrato.
    """
    linhas = bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento)
    if not linhas:
        raise LookupError("Financiamento não encontrado.")

    financiamento = financiamento_de_linha(linhas[0])

    amortizacoes = amortizacoes_de_linhas(
        bd.obtem_dados(conn, 'AmortizacoesExtras',
                       coluna='financiamento_id', valor=id_financiamento)
    )

    indicador = cache_indicadores.obtem_indicador(conn, pedido['indicador'])

    resultado = compara_estrategias(
        financiamento=financiamento,
        amortizacoes_atuais=amortizacoes,
        valor_aporte=pedido['valor_aporte'],
        data_aporte=pedido['data_aporte'],
        taxa_mensal_indicador=indicador['taxa_mensal'],
        percentual_indicador=pedido['percentual_indicador'],
        tipo_amortizacao=pedido['tipo_amortizacao'],
    )

    resultado['indicador'] = indicador['nome']
    resultado['indicador_origem'] = indicador['origem']
    resultado['indicador_referencia'] = indicador['data_referencia']
    return resultado


def _para_persistencia(id_financiamento: int, resultado: dict) -> dict:
    """
    Reduz o resultado da simulação às colunas da tabela Simulacoes.

    Argumentos:
        id_financiamento: contrato associado.
        resultado: dicionário devolvido por `compara_estrategias`.

    Retorna:
        Dicionário pronto para INSERT ou UPDATE.
    """
    registro = {campo: resultado[campo] for campo in CAMPOS_PERSISTIDOS}
    registro['financiamento_id'] = id_financiamento
    registro['obsoleta'] = 0
    registro['criada_em'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    return registro


@bp.route('/<int:id_financiamento>/simulacoes', methods=['POST'])
def criar_simulacao(id_financiamento: int):
    """
    Compara amortizar a dívida com investir o mesmo valor, e salva o resultado.
    ---
    tags:
      - Simulações (amortizar vs. investir)
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [valor_aporte, data_aporte]
          properties:
            valor_aporte:         { type: number,  example: 50000 }
            data_aporte:          { type: string,  example: "2027-06" }
            indicador:            { type: string,  enum: [CDI, SELIC], example: CDI }
            percentual_indicador: { type: number,  example: 100, description: "Percentual do indicador que a aplicação rende (110 = 110% do CDI)." }
            tipo_amortizacao:     { type: string,  enum: [PARCELA, PRAZO], example: PARCELA }
    responses:
      201: { description: Simulação calculada e salva. }
      400: { description: Parâmetros inválidos para este financiamento. }
      404: { description: Financiamento não encontrado. }
      502: { description: Sem taxa disponível — BCB fora do ar e cache vazio. }
    """
    try:
        pedido = _valida_pedido(exige_corpo(request.get_json(silent=True)))
    except ErroValidacao as erro:
        return jsonify({"erro": str(erro)}), 400

    conn = bd.conecta_db()
    try:
        resultado = _executa_simulacao(conn, id_financiamento, pedido)
    except LookupError as erro:
        conn.close()
        return jsonify({"erro": str(erro)}), 404
    except ErroSimulacao as erro:
        conn.close()
        return jsonify({"erro": str(erro)}), 400
    except ErroBCB as erro:
        conn.close()
        return jsonify({"erro": f"Sem taxa de referência disponível: {erro}"}), 502

    id_simulacao = bd.insere_dado(conn, 'Simulacoes', _para_persistencia(id_financiamento, resultado))
    conn.close()

    resultado['id'] = id_simulacao
    resultado['obsoleta'] = 0
    return jsonify(resultado), 201


@bp.route('/<int:id_financiamento>/simulacoes', methods=['GET'])
def listar_simulacoes(id_financiamento: int):
    """
    Lista as simulações salvas de um financiamento, da mais recente para a mais antiga.
    ---
    tags:
      - Simulações (amortizar vs. investir)
    parameters:
      - { in: path,  name: id_financiamento, type: integer, required: true }
      - { in: query, name: veredito, type: string, enum: [AMORTIZAR, INVESTIR, EMPATE], required: false }
    responses:
      200: { description: Lista de simulações salvas. }
      404: { description: Financiamento não encontrado. }
    """
    conn = bd.conecta_db()

    if not bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento):
        conn.close()
        return jsonify({"erro": "Financiamento não encontrado."}), 404

    registros = bd.obtem_dados(
        conn, 'Simulacoes',
        coluna='financiamento_id', valor=id_financiamento, ordem='criada_em DESC',
    )
    conn.close()

    simulacoes = [dict(registro) for registro in registros]

    filtro = request.args.get('veredito')
    if filtro:
        filtro = filtro.upper()
        simulacoes = [s for s in simulacoes if s['veredito'] == filtro]

    return jsonify(simulacoes), 200


@bp.route('/<int:id_financiamento>/simulacoes/<int:id_simulacao>', methods=['PUT'])
def atualizar_simulacao(id_financiamento: int, id_simulacao: int):
    """
    Recalcula uma simulação salva com novos parâmetros e taxa atualizada.
    ---
    tags:
      - Simulações (amortizar vs. investir)
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - { in: path, name: id_simulacao,     type: integer, required: true }
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [valor_aporte, data_aporte]
          properties:
            valor_aporte:         { type: number, example: 80000 }
            data_aporte:          { type: string, example: "2028-01" }
            indicador:            { type: string, enum: [CDI, SELIC], example: SELIC }
            percentual_indicador: { type: number, example: 110 }
            tipo_amortizacao:     { type: string, enum: [PARCELA, PRAZO], example: PRAZO }
    responses:
      200: { description: Simulação recalculada e regravada. }
      400: { description: Parâmetros inválidos para este financiamento. }
      404: { description: Simulação ou financiamento não encontrado. }
      502: { description: Sem taxa disponível — BCB fora do ar e cache vazio. }
    """
    try:
        pedido = _valida_pedido(exige_corpo(request.get_json(silent=True)))
    except ErroValidacao as erro:
        return jsonify({"erro": str(erro)}), 400

    conn = bd.conecta_db()

    existente = bd.obtem_dados(conn, 'Simulacoes', coluna='id', valor=id_simulacao)
    if not existente or existente[0]['financiamento_id'] != id_financiamento:
        conn.close()
        return jsonify({"erro": "Simulação não encontrada neste financiamento."}), 404

    try:
        resultado = _executa_simulacao(conn, id_financiamento, pedido)
    except LookupError as erro:
        conn.close()
        return jsonify({"erro": str(erro)}), 404
    except ErroSimulacao as erro:
        conn.close()
        return jsonify({"erro": str(erro)}), 400
    except ErroBCB as erro:
        conn.close()
        return jsonify({"erro": f"Sem taxa de referência disponível: {erro}"}), 502

    registro = _para_persistencia(id_financiamento, resultado)
    registro.pop('criada_em')  # preserva a data original de criação
    bd.atualiza_dado(conn, 'Simulacoes', registro, valor=id_simulacao)
    conn.close()

    resultado['id'] = id_simulacao
    resultado['obsoleta'] = 0
    return jsonify(resultado), 200


@bp.route('/<int:id_financiamento>/simulacoes/<int:id_simulacao>', methods=['DELETE'])
def deletar_simulacao(id_financiamento: int, id_simulacao: int):
    """
    Remove uma simulação salva.
    ---
    tags:
      - Simulações (amortizar vs. investir)
    parameters:
      - { in: path, name: id_financiamento, type: integer, required: true }
      - { in: path, name: id_simulacao,     type: integer, required: true }
    responses:
      200: { description: Simulação removida. }
      404: { description: Simulação não encontrada neste financiamento. }
    """
    conn = bd.conecta_db()

    existente = bd.obtem_dados(conn, 'Simulacoes', coluna='id', valor=id_simulacao)
    if not existente or existente[0]['financiamento_id'] != id_financiamento:
        conn.close()
        return jsonify({"erro": "Simulação não encontrada neste financiamento."}), 404

    bd.deleta_dados(conn, 'Simulacoes', valor=id_simulacao)
    conn.close()

    return jsonify({"mensagem": "Simulação removida com sucesso."}), 200
