"""
test_calculadora.py
Testes do cálculo dos cronogramas SAC e PRICE.

Estes testes existem porque um erro aqui é silencioso: o cronograma sai com
números plausíveis e ninguém percebe olhando o gráfico. As asserções verificam
identidades contábeis — a soma das amortizações tem que fechar com o valor
financiado, o saldo tem que zerar — que um erro de fórmula quebra na hora.

Execução:
    python -m pytest testes/ -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from servicos.calculadora import (  # noqa: E402
    AmortizacaoExtra,
    Financiamento,
    criar_calculadora,
    meses_entre,
)

VALOR_IMOVEL = 500_000.0
ENTRADA = 100_000.0
FINANCIADO = VALOR_IMOVEL - ENTRADA
TAXA = 0.0089
PRAZO = 360
CENTAVO = 0.01


def contrato(modelo: str = 'SAC') -> Financiamento:
    """Monta um financiamento padrão de referência para os testes."""
    return Financiamento(
        nome='Contrato de teste',
        valor_imovel=VALOR_IMOVEL,
        entrada=ENTRADA,
        taxa_juros=TAXA,
        prazo_meses=PRAZO,
        data_inicio='2026-01',
        modelo=modelo,
    )


@pytest.mark.parametrize('modelo', ['SAC', 'PRICE'])
def test_cronograma_quita_o_saldo(modelo):
    """O saldo devedor da última parcela deve ser zero em ambos os sistemas."""
    parcelas = criar_calculadora(modelo).reconstruir_parcelas(contrato(modelo), [])

    assert len(parcelas) == PRAZO
    assert parcelas[-1].saldo_devedor == 0


@pytest.mark.parametrize('modelo', ['SAC', 'PRICE'])
def test_soma_das_amortizacoes_fecha_com_o_financiado(modelo):
    """A soma das parcelas de principal tem que reconstituir o valor financiado."""
    parcelas = criar_calculadora(modelo).reconstruir_parcelas(contrato(modelo), [])
    total = sum(p.amortizacao for p in parcelas)

    # Tolerância de um centavo por parcela, por conta do arredondamento.
    assert total == pytest.approx(FINANCIADO, abs=PRAZO * CENTAVO)


@pytest.mark.parametrize('modelo', ['SAC', 'PRICE'])
def test_parcela_e_a_soma_de_juros_e_amortizacao(modelo):
    """Cada parcela deve ser exatamente juros mais amortização."""
    parcelas = criar_calculadora(modelo).reconstruir_parcelas(contrato(modelo), [])

    for parcela in parcelas:
        assert parcela.valor_parcela == pytest.approx(
            parcela.juros + parcela.amortizacao, abs=CENTAVO
        )


def test_sac_tem_amortizacao_constante_e_parcela_decrescente():
    """No SAC o principal é fixo e o pagamento total cai mês a mês."""
    parcelas = criar_calculadora('SAC').reconstruir_parcelas(contrato('SAC'), [])

    assert parcelas[0].amortizacao == pytest.approx(FINANCIADO / PRAZO, abs=CENTAVO)
    assert parcelas[0].amortizacao == pytest.approx(parcelas[100].amortizacao, abs=CENTAVO)
    assert parcelas[0].valor_parcela > parcelas[100].valor_parcela > parcelas[-1].valor_parcela


def test_price_tem_parcela_constante_e_amortizacao_crescente():
    """No PRICE o pagamento é fixo e a fatia de principal cresce."""
    parcelas = criar_calculadora('PRICE').reconstruir_parcelas(contrato('PRICE'), [])

    pmt_teorico = FINANCIADO * TAXA / (1 - (1 + TAXA) ** -PRAZO)

    assert parcelas[0].valor_parcela == pytest.approx(pmt_teorico, abs=CENTAVO)
    assert parcelas[0].valor_parcela == pytest.approx(parcelas[200].valor_parcela, abs=CENTAVO)
    assert parcelas[0].amortizacao < parcelas[200].amortizacao < parcelas[-1].amortizacao


def test_price_custa_mais_juros_que_sac():
    """Amortizar mais devagar custa mais caro — o PRICE paga mais juros no total."""
    juros_sac = sum(p.juros for p in criar_calculadora('SAC').reconstruir_parcelas(contrato('SAC'), []))
    juros_price = sum(p.juros for p in criar_calculadora('PRICE').reconstruir_parcelas(contrato('PRICE'), []))

    assert juros_price > juros_sac


def test_amortizacao_tipo_parcela_mantem_o_prazo():
    """Com tipo PARCELA o número de parcelas não muda; o valor de cada uma cai."""
    calculadora = criar_calculadora('SAC')
    base = calculadora.reconstruir_parcelas(contrato(), [])
    com_aporte = calculadora.reconstruir_parcelas(
        contrato(), [AmortizacaoExtra(50_000, '2027-01', 'PARCELA')]
    )

    assert len(com_aporte) == len(base) == PRAZO

    parcela_base = next(p for p in base if p.data_parcela == '2027-02')
    parcela_nova = next(p for p in com_aporte if p.data_parcela == '2027-02')
    assert parcela_nova.valor_parcela < parcela_base.valor_parcela


def test_amortizacao_tipo_prazo_no_sac_encurta_pelo_principal():
    """
    No SAC, o tipo PRAZO congela a parcela de principal.

    Um aporte de R$ 50.000 sobre uma amortização mensal de R$ 1.111,11 tem que
    eliminar exatamente 45 parcelas. Congelar o pagamento *total* em vez do
    principal encurtaria o contrato muito além disso — este teste trava essa
    regressão.
    """
    calculadora = criar_calculadora('SAC')
    com_aporte = calculadora.reconstruir_parcelas(
        contrato(), [AmortizacaoExtra(50_000, '2027-01', 'PRAZO')]
    )

    amortizacao_mensal = FINANCIADO / PRAZO
    parcelas_eliminadas = round(50_000 / amortizacao_mensal)

    assert len(com_aporte) == pytest.approx(PRAZO - parcelas_eliminadas, abs=1)


def test_amortizacao_tipo_prazo_no_price_mantem_a_parcela():
    """No PRICE o tipo PRAZO preserva o valor do pagamento e encurta o contrato."""
    calculadora = criar_calculadora('PRICE')
    base = calculadora.reconstruir_parcelas(contrato('PRICE'), [])
    com_aporte = calculadora.reconstruir_parcelas(
        contrato('PRICE'), [AmortizacaoExtra(50_000, '2027-01', 'PRAZO')]
    )

    assert len(com_aporte) < len(base)

    parcela_base = next(p for p in base if p.data_parcela == '2028-01')
    parcela_nova = next(p for p in com_aporte if p.data_parcela == '2028-01')
    assert parcela_nova.valor_parcela == pytest.approx(parcela_base.valor_parcela, abs=CENTAVO)


def test_aporte_que_cobre_o_saldo_quita_o_contrato():
    """Um aporte maior que o saldo devedor encerra o cronograma na hora."""
    calculadora = criar_calculadora('SAC')
    com_aporte = calculadora.reconstruir_parcelas(
        contrato(), [AmortizacaoExtra(FINANCIADO, '2026-01', 'PRAZO')]
    )

    assert len(com_aporte) == 1
    assert com_aporte[-1].saldo_devedor == 0


def test_amortizacao_produz_efeito_a_partir_do_mes_seguinte():
    """A parcela do mês do aporte já estava em curso e não deve ser afetada."""
    calculadora = criar_calculadora('SAC')
    base = calculadora.reconstruir_parcelas(contrato(), [])
    com_aporte = calculadora.reconstruir_parcelas(
        contrato(), [AmortizacaoExtra(50_000, '2027-01', 'PARCELA')]
    )

    mes_do_aporte_base = next(p for p in base if p.data_parcela == '2027-01')
    mes_do_aporte_novo = next(p for p in com_aporte if p.data_parcela == '2027-01')

    assert mes_do_aporte_novo.valor_parcela == pytest.approx(
        mes_do_aporte_base.valor_parcela, abs=CENTAVO
    )


def test_varias_amortizacoes_sao_aplicadas_em_ordem_cronologica():
    """Uma lista fora de ordem tem que produzir o mesmo resultado que a ordenada."""
    calculadora = criar_calculadora('SAC')
    fora_de_ordem = [
        AmortizacaoExtra(20_000, '2029-03', 'PARCELA'),
        AmortizacaoExtra(30_000, '2027-01', 'PARCELA'),
    ]
    ordenada = list(reversed(fora_de_ordem))

    assert [p.valor_parcela for p in calculadora.reconstruir_parcelas(contrato(), fora_de_ordem)] == \
           [p.valor_parcela for p in calculadora.reconstruir_parcelas(contrato(), ordenada)]


def test_modelo_desconhecido_e_rejeitado():
    """A fábrica não deve inventar um cronograma para um sistema que não existe."""
    with pytest.raises(ValueError, match='não suportado'):
        criar_calculadora('FRANCES')


@pytest.mark.parametrize('inicio, fim, esperado', [
    ('2026-01', '2026-01', 0),
    ('2026-01', '2026-12', 11),
    ('2026-01', '2027-01', 12),
    ('2027-01', '2026-01', -12),
])
def test_meses_entre(inicio, fim, esperado):
    """A contagem de meses precisa atravessar a virada de ano corretamente."""
    assert meses_entre(inicio, fim) == esperado


@pytest.mark.parametrize('modelo', ['SAC', 'PRICE'])
def test_parcela_depois_de_prazo_respeita_o_prazo_encurtado(modelo):
    """
    Uma amortização PARCELA lançada depois de uma PRAZO não pode desfazer o
    encurtamento que a PRAZO produziu.

    O erro era re-espalhar o saldo pelo prazo *original*: o contrato voltava
    a terminar no mês 360, como se a PRAZO nunca tivesse existido. O correto
    é redistribuir sobre o prazo já encurtado.
    """
    calculadora = criar_calculadora(modelo)

    so_prazo = calculadora.reconstruir_parcelas(
        contrato(modelo), [AmortizacaoExtra(50_000, '2027-01', 'PRAZO')]
    )
    prazo_depois_parcela = calculadora.reconstruir_parcelas(
        contrato(modelo), [
            AmortizacaoExtra(50_000, '2027-01', 'PRAZO'),
            AmortizacaoExtra(20_000, '2028-01', 'PARCELA'),
        ]
    )

    # A PARCELA mantém o prazo vigente — que já é o encurtado — e não o original.
    assert len(prazo_depois_parcela) == len(so_prazo)
    assert len(prazo_depois_parcela) < PRAZO
    assert prazo_depois_parcela[-1].saldo_devedor == 0


def test_prazo_sozinho_continua_com_o_mesmo_numero_de_parcelas():
    """A estimativa do prazo efetivo não pode alterar o resultado da PRAZO isolada."""
    calculadora = criar_calculadora('SAC')
    com_aporte = calculadora.reconstruir_parcelas(
        contrato(), [AmortizacaoExtra(50_000, '2027-01', 'PRAZO')]
    )

    amortizacao_mensal = FINANCIADO / PRAZO
    parcelas_eliminadas = round(50_000 / amortizacao_mensal)

    assert len(com_aporte) == pytest.approx(PRAZO - parcelas_eliminadas, abs=1)
