"""
test_rotas.py
Testes de integração das 18 rotas da API.

Rodam contra o cliente de teste do Flask, com um banco SQLite temporário por
sessão — nenhum teste toca o banco de desenvolvimento. As chamadas ao Banco
Central são substituídas por um valor fixo, de modo que a suíte não dependa da
internet nem do BCB estar no ar.

Execução:
    python -m pytest testes/ -v
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONTRATO = {
    'nome': 'Apartamento Botafogo',
    'valor_imovel': 500000,
    'entrada': 100000,
    'taxa_juros': 0.89,
    'prazo_meses': 360,
    'data_inicio': '2026-01',
    'modelo': 'SAC',
}


@pytest.fixture(scope='session')
def cliente():
    """
    Sobe a aplicação com um banco temporário e o BCB simulado.

    O DB_PATH precisa ser definido antes de importar `app`, porque o módulo
    inicializa o banco no escopo de import.
    """
    with tempfile.TemporaryDirectory() as pasta:
        os.environ['DB_PATH'] = os.path.join(pasta, 'teste.db')

        from servicos import cliente_bcb

        def serie_simulada(codigo, ultimos=12):
            """Devolve uma série sintética estável, sem tocar a rede."""
            return [
                {'data_referencia': f'2026-{mes:02d}', 'valor': 0.0098}
                for mes in range(1, min(ultimos, 12) + 1)
            ]

        cliente_bcb.busca_serie = serie_simulada

        import app as aplicacao

        aplicacao.app.config['TESTING'] = True
        with aplicacao.app.test_client() as teste:
            yield teste


@pytest.fixture
def id_financiamento(cliente):
    """Cria um financiamento descartável e devolve seu identificador."""
    contador = getattr(id_financiamento, 'contador', 0) + 1
    id_financiamento.contador = contador

    corpo = {**CONTRATO, 'nome': f"{CONTRATO['nome']} {contador}"}
    resposta = cliente.post('/financiamento/', json=corpo)

    assert resposta.status_code == 201
    identificador = resposta.get_json()['id']

    yield identificador

    cliente.delete(f'/financiamento/{identificador}')


# ── Infraestrutura ───────────────────────────────────────────────────────────

def test_health_responde_200_mesmo_com_bcb_fora(cliente, monkeypatch):
    """
    O healthcheck não pode depender da componente externa.

    Se o BCB derrubasse o /health, o container ficaria unhealthy e o front
    nunca subiria — por um motivo fora do controle da aplicação.
    """
    from servicos import cliente_bcb

    def falha(*_args, **_kwargs):
        raise cliente_bcb.ErroBCB('simulando BCB fora do ar')

    monkeypatch.setattr('app.busca_serie', falha)

    resposta = cliente.get('/health')

    assert resposta.status_code == 200
    assert resposta.get_json()['api'] == 'ok'
    assert resposta.get_json()['bcb'] == 'indisponivel'


def test_rota_inexistente_devolve_json(cliente):
    """O 404 do Flask tem que sair no mesmo formato das demais rotas."""
    resposta = cliente.get('/rota-que-nao-existe')

    assert resposta.status_code == 404
    assert 'erro' in resposta.get_json()


# ── Financiamentos: os quatro métodos ────────────────────────────────────────

def test_post_financiamento_gera_o_cronograma(cliente, id_financiamento):
    """Criar o contrato tem que produzir as 360 parcelas de uma vez."""
    resposta = cliente.get(f'/financiamento/{id_financiamento}/parcelas')

    assert resposta.status_code == 200
    assert len(resposta.get_json()) == 360


def test_post_financiamento_rejeita_entrada_maior_que_o_imovel(cliente):
    """Entrada acima do valor do imóvel não é um contrato válido."""
    resposta = cliente.post('/financiamento/', json={
        **CONTRATO, 'nome': 'Contrato inválido', 'entrada': 600000,
    })

    assert resposta.status_code == 400
    assert 'entrada' in resposta.get_json()['erro'].lower()


def test_post_financiamento_rejeita_nome_duplicado(cliente, id_financiamento):
    """O nome é a chave natural do contrato; duplicá-lo devolve 409."""
    nome = cliente.get(f'/financiamento/{id_financiamento}').get_json()['nome']
    resposta = cliente.post('/financiamento/', json={**CONTRATO, 'nome': nome})

    assert resposta.status_code == 409


def test_get_financiamentos_pagina_e_ordena(cliente, id_financiamento):
    """A listagem devolve itens e metadados de paginação."""
    resposta = cliente.get('/financiamento/?ordenar_por=valor_imovel&ordem=desc&por_pagina=5')
    corpo = resposta.get_json()

    assert resposta.status_code == 200
    assert 'itens' in corpo and 'paginacao' in corpo
    assert corpo['paginacao']['por_pagina'] == 5
    assert len(corpo['itens']) <= 5


def test_get_financiamentos_filtra_por_modelo(cliente, id_financiamento):
    """O filtro de modelo não pode devolver contratos de outro sistema."""
    resposta = cliente.get('/financiamento/?modelo=PRICE')

    assert resposta.status_code == 200
    assert all(item['modelo'] == 'PRICE' for item in resposta.get_json()['itens'])


def test_get_financiamentos_bloqueia_ordenacao_arbitraria(cliente):
    """
    A allowlist de colunas é a defesa contra injeção de SQL.

    `ordenar_por` é interpolado na query; aceitar texto livre aqui permitiria
    encerrar a instrução e anexar outra.
    """
    resposta = cliente.get('/financiamento/?ordenar_por=nome;DROP TABLE Financiamentos')

    assert resposta.status_code == 400


def test_put_financiamento_recalcula_o_cronograma(cliente, id_financiamento):
    """Editar o prazo tem que regravar o cronograma inteiro."""
    resposta = cliente.put(f'/financiamento/{id_financiamento}', json={
        **CONTRATO,
        'nome': f'Editado {id_financiamento}',
        'prazo_meses': 240,
        'modelo': 'PRICE',
    })

    assert resposta.status_code == 200
    assert resposta.get_json()['parcelas_geradas'] == 240

    parcelas = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()
    assert len(parcelas) == 240
    assert parcelas[0]['valor_parcela'] == pytest.approx(parcelas[-1]['valor_parcela'], abs=0.02)


def test_put_financiamento_inexistente_devolve_404(cliente):
    """Editar o que não existe não pode criar nada."""
    resposta = cliente.put('/financiamento/999999', json=CONTRATO)

    assert resposta.status_code == 404


def test_delete_financiamento_remove_em_cascata(cliente):
    """Apagar o contrato tem que levar junto parcelas, amortizações e simulações."""
    criado = cliente.post('/financiamento/', json={**CONTRATO, 'nome': 'Para deletar'})
    identificador = criado.get_json()['id']

    cliente.post(f'/financiamento/{identificador}/amortizacoes', json={
        'valor_amortizado': 20000, 'data_amortizacao': '2027-01', 'tipo': 'PARCELA',
    })

    assert cliente.delete(f'/financiamento/{identificador}').status_code == 200
    assert cliente.get(f'/financiamento/{identificador}').status_code == 404
    assert cliente.get(f'/financiamento/{identificador}/parcelas').status_code == 404


# ── Parcelas ─────────────────────────────────────────────────────────────────

def test_get_parcelas_filtra_por_ano(cliente, id_financiamento):
    """O filtro por ano devolve no máximo doze competências."""
    parcelas = cliente.get(f'/financiamento/{id_financiamento}/parcelas?ano=2027').get_json()

    assert len(parcelas) == 12
    assert all(p['data_parcela'].startswith('2027') for p in parcelas)


def test_get_parcelas_agrupadas_por_ano(cliente, id_financiamento):
    """O agrupamento consolida os totais anuais sem perder parcelas."""
    anos = cliente.get(f'/financiamento/{id_financiamento}/parcelas?agrupar=ano').get_json()

    assert sum(ano['parcelas'] for ano in anos) == 360
    assert anos == sorted(anos, key=lambda a: a['ano'])


def test_get_resumo_de_parcelas(cliente, id_financiamento):
    """No SAC a primeira parcela é a maior e a última, a menor."""
    resumo = cliente.get(f'/financiamento/{id_financiamento}/parcelas/resumo').get_json()

    assert resumo['total_parcelas'] == 360
    assert resumo['total_amortizado'] == pytest.approx(400000, abs=5)
    assert resumo['maior_parcela'] > resumo['menor_parcela']


# ── Amortizações: os quatro métodos ──────────────────────────────────────────

def test_post_amortizacao_reduz_as_parcelas_seguintes(cliente, id_financiamento):
    """Com tipo PARCELA, o prazo se mantém e o valor mensal cai."""
    antes = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()
    parcela_antes = next(p for p in antes if p['data_parcela'] == '2027-02')

    resposta = cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json={
        'valor_amortizado': 50000, 'data_amortizacao': '2027-01', 'tipo': 'PARCELA',
    })
    assert resposta.status_code == 201

    depois = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()
    parcela_depois = next(p for p in depois if p['data_parcela'] == '2027-02')

    assert len(depois) == len(antes)
    assert parcela_depois['valor_parcela'] < parcela_antes['valor_parcela']


def test_post_amortizacao_tipo_prazo_encurta_o_contrato(cliente, id_financiamento):
    """Com tipo PRAZO, o cronograma termina antes."""
    cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json={
        'valor_amortizado': 50000, 'data_amortizacao': '2027-01', 'tipo': 'PRAZO',
    })

    parcelas = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()

    assert len(parcelas) < 360


def test_post_amortizacao_rejeita_data_duplicada(cliente, id_financiamento):
    """Duas amortizações na mesma competência não fazem sentido no modelo."""
    corpo = {'valor_amortizado': 10000, 'data_amortizacao': '2028-05', 'tipo': 'PARCELA'}

    assert cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json=corpo).status_code == 201
    assert cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json=corpo).status_code == 409


def test_post_amortizacao_rejeita_data_anterior_ao_contrato(cliente, id_financiamento):
    """Não se amortiza um financiamento que ainda não começou."""
    resposta = cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json={
        'valor_amortizado': 10000, 'data_amortizacao': '2020-01', 'tipo': 'PARCELA',
    })

    assert resposta.status_code == 400


def test_get_amortizacoes_lista_em_ordem(cliente, id_financiamento):
    """A listagem sai em ordem cronológica."""
    for competencia in ('2029-01', '2027-01', '2028-01'):
        cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json={
            'valor_amortizado': 10000, 'data_amortizacao': competencia, 'tipo': 'PARCELA',
        })

    amortizacoes = cliente.get(f'/financiamento/{id_financiamento}/amortizacoes').get_json()
    datas = [a['data_amortizacao'] for a in amortizacoes]

    assert datas == sorted(datas)


def test_put_amortizacao_reconstroi_o_cronograma(cliente, id_financiamento):
    """Alterar o valor da amortização muda as parcelas seguintes."""
    criada = cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json={
        'valor_amortizado': 20000, 'data_amortizacao': '2027-01', 'tipo': 'PARCELA',
    })
    id_amortizacao = criada.get_json()['id']

    antes = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()
    parcela_antes = next(p for p in antes if p['data_parcela'] == '2027-06')

    resposta = cliente.put(
        f'/financiamento/{id_financiamento}/amortizacoes/{id_amortizacao}',
        json={'valor_amortizado': 90000, 'data_amortizacao': '2027-01', 'tipo': 'PARCELA'},
    )
    assert resposta.status_code == 200

    depois = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()
    parcela_depois = next(p for p in depois if p['data_parcela'] == '2027-06')

    assert parcela_depois['valor_parcela'] < parcela_antes['valor_parcela']


def test_put_amortizacao_de_outro_financiamento_devolve_404(cliente, id_financiamento):
    """Uma amortização só pode ser editada pelo contrato que a contém."""
    resposta = cliente.put(
        f'/financiamento/{id_financiamento}/amortizacoes/999999',
        json={'valor_amortizado': 1000, 'data_amortizacao': '2027-01', 'tipo': 'PARCELA'},
    )

    assert resposta.status_code == 404


def test_delete_amortizacao_restaura_o_cronograma(cliente, id_financiamento):
    """Remover a amortização tem que devolver as parcelas ao valor original."""
    original = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()

    criada = cliente.post(f'/financiamento/{id_financiamento}/amortizacoes', json={
        'valor_amortizado': 50000, 'data_amortizacao': '2027-01', 'tipo': 'PARCELA',
    })
    id_amortizacao = criada.get_json()['id']

    assert cliente.delete(
        f'/financiamento/{id_financiamento}/amortizacoes/{id_amortizacao}'
    ).status_code == 200

    restaurado = cliente.get(f'/financiamento/{id_financiamento}/parcelas').get_json()

    assert len(restaurado) == len(original)
    assert restaurado[100]['valor_parcela'] == pytest.approx(original[100]['valor_parcela'], abs=0.02)


# ── Indicadores: a componente externa ────────────────────────────────────────

def test_get_indicadores_traz_as_tres_series(cliente):
    """A rota consolida CDI, Selic e IPCA já tratados."""
    corpo = cliente.get('/indicadores/').get_json()
    nomes = {indicador['nome'] for indicador in corpo['indicadores']}

    assert nomes == {'CDI', 'SELIC', 'IPCA'}
    assert corpo['origem'] in ('bcb', 'cache', 'misto')
    assert corpo['origem'] != 'cache_vencido'


def test_get_indicadores_normaliza_a_selic_para_base_mensal(cliente):
    """
    A Selic vem anualizada do BCB e a taxa mensal tem que ser bem menor.

    Tratar o valor anual como mensal infla qualquer projeção em ordens de
    grandeza — este teste é a trava contra esse erro.
    """
    corpo = cliente.get('/indicadores/').get_json()
    selic = next(i for i in corpo['indicadores'] if i['nome'] == 'SELIC')

    assert selic['periodicidade'] == 'anual'
    assert selic['taxa_mensal'] < selic['valor']
    assert (1 + selic['taxa_mensal']) ** 12 == pytest.approx(1 + selic['valor'], rel=1e-6)


def test_get_historico_de_indicador(cliente):
    """O histórico sai em ordem cronológica crescente."""
    corpo = cliente.get('/indicadores/CDI/historico?ultimos=6').get_json()
    datas = [o['data_referencia'] for o in corpo['historico']]

    assert corpo['nome'] == 'CDI'
    assert datas == sorted(datas)


def test_get_historico_de_indicador_inexistente(cliente):
    """Um indicador fora do catálogo é erro do cliente, não do servidor."""
    assert cliente.get('/indicadores/BITCOIN/historico').status_code == 400


def test_post_atualizar_indicadores(cliente):
    """A releitura forçada regrava o cache com o dado corrente."""
    resposta = cliente.post('/indicadores/atualizar')

    assert resposta.status_code == 200
    assert set(resposta.get_json()['atualizados']) == {'CDI', 'SELIC', 'IPCA'}


# ── Simulações: os quatro métodos ────────────────────────────────────────────

def test_post_simulacao_devolve_veredito(cliente, id_financiamento):
    """A simulação compara os dois montantes e conclui."""
    resposta = cliente.post(f'/financiamento/{id_financiamento}/simulacoes', json={
        'valor_aporte': 50000, 'data_aporte': '2027-01', 'indicador': 'CDI',
    })
    corpo = resposta.get_json()

    assert resposta.status_code == 201
    assert corpo['veredito'] in ('AMORTIZAR', 'INVESTIR', 'EMPATE')
    assert corpo['montante_amortizar'] > 0
    assert corpo['taxa_mensal'] > 0


def test_post_simulacao_rejeita_aporte_maior_que_o_saldo(cliente, id_financiamento):
    """Não dá para amortizar mais do que se deve."""
    resposta = cliente.post(f'/financiamento/{id_financiamento}/simulacoes', json={
        'valor_aporte': 900000, 'data_aporte': '2027-01',
    })

    assert resposta.status_code == 400


def test_get_simulacoes_lista_as_salvas(cliente, id_financiamento):
    """Cada simulação criada aparece no histórico do contrato."""
    for aporte in (30000, 60000):
        cliente.post(f'/financiamento/{id_financiamento}/simulacoes', json={
            'valor_aporte': aporte, 'data_aporte': '2027-01',
        })

    simulacoes = cliente.get(f'/financiamento/{id_financiamento}/simulacoes').get_json()

    assert len(simulacoes) == 2
    assert all(s['obsoleta'] == 0 for s in simulacoes)


def test_put_simulacao_recalcula_com_novos_parametros(cliente, id_financiamento):
    """Trocar o aporte muda os montantes da simulação salva."""
    criada = cliente.post(f'/financiamento/{id_financiamento}/simulacoes', json={
        'valor_aporte': 30000, 'data_aporte': '2027-01',
    }).get_json()

    resposta = cliente.put(
        f"/financiamento/{id_financiamento}/simulacoes/{criada['id']}",
        json={'valor_aporte': 90000, 'data_aporte': '2027-01', 'percentual_indicador': 110},
    )
    corpo = resposta.get_json()

    assert resposta.status_code == 200
    assert corpo['valor_aporte'] == 90000
    assert corpo['percentual_indicador'] == pytest.approx(1.1)
    assert corpo['montante_investir'] > criada['montante_investir']


def test_delete_simulacao(cliente, id_financiamento):
    """Remover a simulação a tira do histórico."""
    criada = cliente.post(f'/financiamento/{id_financiamento}/simulacoes', json={
        'valor_aporte': 30000, 'data_aporte': '2027-01',
    }).get_json()

    assert cliente.delete(
        f"/financiamento/{id_financiamento}/simulacoes/{criada['id']}"
    ).status_code == 200
    assert cliente.get(f'/financiamento/{id_financiamento}/simulacoes').get_json() == []


def test_editar_o_contrato_marca_as_simulacoes_como_obsoletas(cliente, id_financiamento):
    """
    Uma simulação salva descreve um cronograma específico.

    Se o contrato muda, aqueles montantes deixam de ser reproduzíveis — em vez
    de sumirem, ficam sinalizados para a interface poder avisar o usuário.
    """
    cliente.post(f'/financiamento/{id_financiamento}/simulacoes', json={
        'valor_aporte': 30000, 'data_aporte': '2027-01',
    })

    cliente.put(f'/financiamento/{id_financiamento}', json={
        **CONTRATO, 'nome': f'Renomeado {id_financiamento}', 'taxa_juros': 0.70,
    })

    simulacoes = cliente.get(f'/financiamento/{id_financiamento}/simulacoes').get_json()

    assert all(s['obsoleta'] == 1 for s in simulacoes)


def test_cache_dentro_do_ttl_nao_e_reportado_como_falha(cliente):
    """
    Servir do cache dentro do TTL é o funcionamento normal, não uma degradação.

    Se essa distinção se perder, a interface passa a exibir "o Banco Central
    não respondeu" em toda carga de página depois da primeira — um alarme
    falso, já que o dado veio do BCB e só não foi relido.
    """
    cliente.post('/indicadores/atualizar')

    corpo = cliente.get('/indicadores/').get_json()

    assert corpo['origem'] in ('bcb', 'cache', 'misto')
    assert corpo['origem'] != 'cache_vencido'


def test_bcb_fora_do_ar_marca_o_cache_como_vencido(cliente, monkeypatch):
    """Com o BCB inacessível e o TTL expirado, a resposta precisa se declarar degradada."""
    from servicos import cache_indicadores, cliente_bcb

    cliente.post('/indicadores/atualizar')  # garante algo no cache

    def falha(*_args, **_kwargs):
        raise cliente_bcb.ErroBCB('simulando BCB fora do ar')

    monkeypatch.setattr('servicos.cache_indicadores.busca_indicador', falha)
    monkeypatch.setattr(cache_indicadores, 'CACHE_TTL_HORAS', 0)

    corpo = cliente.get('/indicadores/').get_json()

    assert corpo['origem'] == 'cache_vencido'
    # Mesmo degradada, a resposta continua útil: os valores seguem lá.
    assert all(i.get('valor') is not None for i in corpo['indicadores'])
