"""
app.py
Ponto de entrada da API do Simulador de Financiamento Imobiliário.

A inicialização do banco fica no escopo de módulo, e não dentro de
`if __name__ == '__main__'`: sob gunicorn esse bloco nunca executa, e o banco
jamais seria criado no container.
"""

import os

from flasgger import Swagger
from flask import Flask, jsonify
from flask_cors import CORS

import banco_de_dados as bd
from rotas.amortizacoes import bp as amortizacoes_bp
from rotas.financiamento import bp as financiamento_bp
from rotas.indicadores import bp as indicadores_bp
from rotas.parcelas import bp as parcelas_bp
from rotas.simulacoes import bp as simulacoes_bp
from servicos.cliente_bcb import ErroBCB, busca_serie

#: Metadados exibidos na página do Swagger em /apidocs.
CONFIG_SWAGGER = {
    'title': 'Amortiza ou Investe? — API',
    'uiversion': 3,
    'description': (
        'API REST que calcula cronogramas de financiamento pelos sistemas SAC e '
        'PRICE, simula amortizações extraordinárias e compara a decisão de '
        'amortizar a dívida com a de investir o mesmo valor à taxa de mercado, '
        'usando as séries do Banco Central do Brasil.'
    ),
    'version': '2.0.0',
}

app = Flask(__name__)
CORS(app)
Swagger(app, template={'info': CONFIG_SWAGGER})

app.register_blueprint(financiamento_bp)
app.register_blueprint(parcelas_bp)
app.register_blueprint(amortizacoes_bp)
app.register_blueprint(indicadores_bp)
app.register_blueprint(simulacoes_bp)

# Idempotente: cria as tabelas que ainda não existirem, a cada boot do worker.
bd.inicializa_db()


@app.route('/', methods=['GET'])
def raiz():
    """
    Apresenta a API e aponta os caminhos úteis.
    ---
    tags:
      - Infraestrutura
    responses:
      200:
        description: |
          Índice da API. Existe para que quem abrir a raiz no navegador —
          esperando a interface, por exemplo — receba uma orientação em vez
          de um 404 seco.
    """
    return jsonify({
        'nome': CONFIG_SWAGGER['title'],
        'versao': CONFIG_SWAGGER['version'],
        'descricao': CONFIG_SWAGGER['description'],
        'documentacao': '/apidocs',
        'health': '/health',
        'recursos': {
            'financiamentos': '/financiamento/',
            'indicadores': '/indicadores/',
        },
        'interface': 'A interface web roda em http://localhost:8080',
        'componente_externa': 'SGS — Banco Central do Brasil (api.bcb.gov.br)',
    }), 200


@app.route('/health', methods=['GET'])
def health():
    """
    Verifica se a API está no ar e se o Banco Central está acessível.
    ---
    tags:
      - Infraestrutura
    responses:
      200:
        description: |
          A API está operante. O campo `bcb` informa se a componente externa
          responde, mas uma indisponibilidade dela **não** altera o status HTTP:
          o sistema continua funcional servindo indicadores do cache local.
    """
    try:
        busca_serie(4390, ultimos=1)
        status_bcb = 'ok'
    except ErroBCB:
        status_bcb = 'indisponivel'

    return jsonify({
        'api': 'ok',
        'bcb': status_bcb,
        'versao': CONFIG_SWAGGER['version'],
    }), 200


@app.errorhandler(404)
def rota_nao_encontrada(_erro):
    """Uniformiza o 404 do Flask com o formato de erro das demais rotas."""
    return jsonify({'erro': 'Rota não encontrada. Consulte a documentação em /apidocs.'}), 404


@app.errorhandler(405)
def metodo_nao_permitido(_erro):
    """Uniformiza o 405 do Flask com o formato de erro das demais rotas."""
    return jsonify({'erro': 'Método HTTP não permitido nesta rota.'}), 405


@app.errorhandler(500)
def erro_interno(_erro):
    """Evita vazar stack trace para o cliente em caso de falha não prevista."""
    return jsonify({'erro': 'Erro interno no servidor.'}), 500


if __name__ == '__main__':
    app.run(
        host=os.getenv('FLASK_HOST', '127.0.0.1'),
        port=int(os.getenv('FLASK_PORT', '5000')),
        debug=os.getenv('FLASK_DEBUG', '1') == '1',
    )
