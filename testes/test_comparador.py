"""
test_comparador.py
Testes da comparação "amortizar ou investir".

Esta é a parte do sistema onde um erro é mais perigoso: o resultado sai sempre
plausível, com duas cifras grandes e um veredito convincente, mesmo quando a
fórmula está errada. Os testes abaixo travam as propriedades que precisam valer
qualquer que seja o cenário — principalmente a monotonicidade em relação à taxa
e a ausência de dupla contagem do principal.

Execução:
    python -m pytest testes/ -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from servicos.calculadora import AmortizacaoExtra, Financiamento  # noqa: E402
from servicos.comparador import (  # noqa: E402
    ErroSimulacao,
    aliquota_ir,
    compara_estrategias,
)

APORTE = 50_000.0
DATA_APORTE = '2027-01'


def contrato(modelo: str = 'SAC') -> Financiamento:
    """Financiamento de referência: R$ 400 mil em 360 meses a 0,89% a.m."""
    return Financiamento(
        nome='Contrato de teste',
        valor_imovel=500_000.0,
        entrada=100_000.0,
        taxa_juros=0.0089,
        prazo_meses=360,
        data_inicio='2026-01',
        modelo=modelo,
    )


@pytest.mark.parametrize('meses, esperado', [
    (1, 0.225),    # 30 dias
    (6, 0.225),    # 180 dias — limite da primeira faixa
    (7, 0.200),    # 210 dias
    (12, 0.200),   # 360 dias — limite da segunda faixa
    (13, 0.175),   # 390 dias
    (24, 0.175),   # 720 dias — limite da terceira faixa
    (25, 0.150),   # 750 dias
    (360, 0.150),
])
def test_aliquota_ir_segue_as_faixas_por_dias_corridos(meses, esperado):
    """
    A tabela do IR sobre renda fixa é definida por dias, não por meses.

    Enquadrar por mês faz a faixa virar cedo demais e superestima o rendimento
    líquido do lado "investir".
    """
    assert aliquota_ir(meses) == esperado


def test_taxa_baixa_favorece_amortizar():
    """Se o dinheiro rende pouco, abater a dívida cara é o melhor destino."""
    resultado = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, taxa_mensal_indicador=0.003)

    assert resultado['veredito'] == 'AMORTIZAR'
    assert resultado['montante_amortizar'] > resultado['montante_investir']


def test_taxa_alta_favorece_investir():
    """Se o dinheiro rende bem acima dos juros do contrato, investir ganha."""
    resultado = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, taxa_mensal_indicador=0.020)

    assert resultado['veredito'] == 'INVESTIR'
    assert resultado['montante_investir'] > resultado['montante_amortizar']


def test_montante_investido_cresce_com_a_taxa():
    """O lado "investir" tem que ser estritamente crescente na taxa."""
    taxas = [0.002, 0.005, 0.008, 0.012, 0.018, 0.025]
    montantes = [
        compara_estrategias(contrato(), [], APORTE, DATA_APORTE, taxa_mensal_indicador=taxa)['montante_investir']
        for taxa in taxas
    ]

    assert montantes == sorted(montantes)
    assert len(set(montantes)) == len(montantes)


def test_veredito_vira_uma_unica_vez_conforme_a_taxa_sobe():
    """
    Existe um ponto de indiferença, e ele é único.

    A vantagem de amortizar *não* é monotônica na taxa, e isso é esperado: as
    parcelas economizadas também são reinvestidas, então os dois lados crescem
    junto com a taxa. O que precisa valer é que, uma vez que investir passa a
    ganhar, ele não volta a perder — se o veredito oscilasse, haveria erro de
    sinal ou de capitalização em algum dos lados.
    """
    taxas = [0.001 * passo for passo in range(1, 31)]
    vereditos = [
        compara_estrategias(contrato(), [], APORTE, DATA_APORTE, taxa_mensal_indicador=taxa)['veredito']
        for taxa in taxas
    ]

    investir_ganha = [v == 'INVESTIR' for v in vereditos]
    viradas = sum(
        1 for anterior, atual in zip(investir_ganha, investir_ganha[1:])
        if anterior != atual
    )

    assert viradas == 1, f"O veredito oscilou {viradas} vezes: {vereditos}"
    assert investir_ganha[0] is False
    assert investir_ganha[-1] is True


def test_percentual_do_indicador_melhora_o_lado_investir():
    """Render 130% do CDI não pode ser pior do que render 100% do mesmo CDI."""
    cem = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, 0.009, percentual_indicador=1.0)
    cento_e_trinta = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, 0.009, percentual_indicador=1.3)

    assert cento_e_trinta['montante_investir'] > cem['montante_investir']


def test_juros_evitados_nao_contam_o_aporte():
    """
    Os juros evitados são a economia *líquida* do principal devolvido.

    A soma bruta das parcelas economizadas embute o próprio aporte; se o valor
    reportado fosse essa soma, ele começaria em R$ 50.000 mesmo com juros zero.
    """
    resultado = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, 0.009)

    soma_bruta = sum(item['economia'] for item in resultado['fluxo_economia'])

    assert resultado['juros_evitados'] == pytest.approx(soma_bruta - APORTE, abs=1.0)
    assert resultado['juros_evitados'] < soma_bruta


def test_aporte_maior_que_o_saldo_e_rejeitado():
    """Não dá para amortizar mais do que se deve."""
    with pytest.raises(ErroSimulacao, match='supera o saldo devedor'):
        compara_estrategias(contrato(), [], 900_000, DATA_APORTE, 0.009)


def test_aporte_depois_do_fim_do_contrato_e_rejeitado():
    """Sem meses restantes não há o que comparar."""
    with pytest.raises(ErroSimulacao, match='já estaria quitado'):
        compara_estrategias(contrato(), [], APORTE, '2090-01', 0.009)


def test_aporte_antes_do_inicio_do_contrato_e_rejeitado():
    """A simulação precisa cair dentro da vigência do financiamento."""
    with pytest.raises(ErroSimulacao, match='anterior ao início'):
        compara_estrategias(contrato(), [], APORTE, '2020-01', 0.009)


def test_aporte_nao_positivo_e_rejeitado():
    """Um aporte de zero ou negativo não representa nenhuma decisão."""
    with pytest.raises(ErroSimulacao, match='deve ser positivo'):
        compara_estrategias(contrato(), [], 0, DATA_APORTE, 0.009)


def test_simulacao_considera_amortizacoes_ja_existentes():
    """
    O cenário base precisa incluir as amortizações já lançadas.

    Ignorá-las superestimaria a economia do novo aporte, porque ele apareceria
    abatendo um saldo maior do que o real.
    """
    ja_lancada = [AmortizacaoExtra(100_000, '2026-06', 'PARCELA')]

    com_historico = compara_estrategias(contrato(), ja_lancada, APORTE, DATA_APORTE, 0.009)
    sem_historico = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, 0.009)

    assert com_historico['montante_amortizar'] != sem_historico['montante_amortizar']


@pytest.mark.parametrize('modelo', ['SAC', 'PRICE'])
def test_simulacao_funciona_nos_dois_sistemas(modelo):
    """A comparação não pode depender do sistema de amortização do contrato."""
    resultado = compara_estrategias(contrato(modelo), [], APORTE, DATA_APORTE, 0.009)

    assert resultado['veredito'] in ('AMORTIZAR', 'INVESTIR', 'EMPATE')
    assert resultado['montante_amortizar'] > 0
    assert resultado['montante_investir'] > 0


@pytest.mark.parametrize('tipo', ['PARCELA', 'PRAZO'])
def test_simulacao_aceita_os_dois_efeitos_de_amortizacao(tipo):
    """Reduzir parcela e reduzir prazo são decisões diferentes, ambas simuláveis."""
    resultado = compara_estrategias(
        contrato(), [], APORTE, DATA_APORTE, 0.009, tipo_amortizacao=tipo,
    )

    assert resultado['prazo_original'] == 360

    if tipo == 'PRAZO':
        assert resultado['prazo_com_aporte'] < resultado['prazo_original']
    else:
        assert resultado['prazo_com_aporte'] == resultado['prazo_original']


def test_resultado_expoe_os_parametros_para_reproducao():
    """
    Uma simulação salva precisa carregar tudo que permite refazer a conta.

    Sem a taxa congelada, o número exibido amanhã não bate com o de hoje.
    """
    resultado = compara_estrategias(contrato(), [], APORTE, DATA_APORTE, 0.009, percentual_indicador=1.1)

    for campo in ('taxa_mensal', 'percentual_indicador', 'aliquota_ir', 'meses_restantes'):
        assert campo in resultado

    assert resultado['taxa_mensal'] == pytest.approx(0.009)
    assert resultado['percentual_indicador'] == pytest.approx(1.1)
