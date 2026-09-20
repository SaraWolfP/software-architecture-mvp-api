"""
comparador.py
A regra de negócio que dá sentido à componente externa: dado um financiamento
em curso e uma sobra de caixa, vale mais a pena amortizar a dívida ou investir
o dinheiro à taxa de mercado?

## Como a comparação é montada

O erro mais comum ao responder essa pergunta é somar as parcelas economizadas e
comparar com o rendimento do investimento. Isso é inválido por dois motivos:

1. A diferença entre os cronogramas contém o próprio aporte — os R$ 50 mil saem
   das parcelas futuras —, enquanto o rendimento não contém o principal. Somar
   grandezas diferentes faz o veredito pender sempre para amortizar.
2. Parcelas economizadas ao longo de 20 anos e um montante resgatado no fim do
   período não estão no mesmo instante do tempo.

A formulação usada aqui evita os dois problemas colocando as duas estratégias no
mesmo ponto de partida e no mesmo ponto de chegada:

    Ponto de partida  R$ A disponíveis na competência do aporte.
    Ponto de chegada  a competência da última parcela do contrato original.

    Estratégia AMORTIZAR   gasta A abatendo o saldo devedor. A cada mês seguinte
                           sobra `parcela_original − parcela_nova`, que é
                           aplicada à taxa líquida até o fim do horizonte.
    Estratégia INVESTIR    aplica A à taxa do indicador pelo horizonte inteiro,
                           paga Imposto de Renda no resgate, e segue pagando as
                           parcelas originais.

Ambas terminam com um montante em caixa na mesma data. Esses dois montantes são
diretamente comparáveis — é essa a comparação que o sistema apresenta.

## Imposto de Renda

A alíquota da renda fixa é regressiva e definida por **dias corridos**, não por
meses. Ignorá-la superestima o lado "investir" em até 22,5% do rendimento.
"""

from __future__ import annotations

from servicos.calculadora import (
    AmortizacaoExtra,
    Financiamento,
    criar_calculadora,
    meses_entre,
)

#: Faixas do IR sobre renda fixa: (limite em dias corridos, alíquota).
FAIXAS_IR = (
    (180, 0.225),
    (360, 0.200),
    (720, 0.175),
    (float('inf'), 0.150),
)

#: Margem, como fração do aporte, dentro da qual as estratégias são consideradas equivalentes.
MARGEM_EMPATE = 0.01

#: Dias corridos considerados por mês no enquadramento do IR.
DIAS_POR_MES = 30


class ErroSimulacao(ValueError):
    """Parâmetros de simulação inconsistentes com o financiamento."""


def aliquota_ir(meses: int) -> float:
    """
    Determina a alíquota de IR sobre renda fixa para um prazo de aplicação.

    Argumentos:
        meses: prazo da aplicação em meses.

    Retorna:
        Alíquota em decimal (0.15 a 0.225).

    Exemplos:
        aliquota_ir(5)   → 0.225   (150 dias)
        aliquota_ir(360) → 0.15
    """
    dias = max(meses, 0) * DIAS_POR_MES
    for limite, aliquota in FAIXAS_IR:
        if dias <= limite:
            return aliquota
    return FAIXAS_IR[-1][1]


def _parcelas_por_competencia(parcelas) -> dict[str, float]:
    """
    Indexa um cronograma por competência, para permitir a comparação mês a mês.

    Argumentos:
        parcelas: lista de Parcela.

    Retorna:
        Dicionário 'YYYY-MM' -> valor da parcela.
    """
    return {p.data_parcela: p.valor_parcela for p in parcelas}


def compara_estrategias(
    financiamento: Financiamento,
    amortizacoes_atuais: list[AmortizacaoExtra],
    valor_aporte: float,
    data_aporte: str,
    taxa_mensal_indicador: float,
    percentual_indicador: float = 1.0,
    tipo_amortizacao: str = 'PARCELA',
) -> dict:
    """
    Compara amortizar a dívida com investir o mesmo valor à taxa de mercado.

    Argumentos:
        financiamento: contrato em curso.
        amortizacoes_atuais: amortizações já lançadas (entram nos dois cenários).
        valor_aporte: quantia disponível, em reais.
        data_aporte: competência do aporte, 'YYYY-MM'.
        taxa_mensal_indicador: taxa mensal do indicador em decimal, já
            normalizada por `cliente_bcb.para_taxa_mensal`.
        percentual_indicador: fração do indicador que a aplicação rende
            (1.0 = 100% do CDI, 1.1 = 110% do CDI).
        tipo_amortizacao: 'PARCELA' ou 'PRAZO' — o efeito simulado do aporte.

    Retorna:
        Dicionário com os dois montantes finais, o veredito, a diferença e os
        parâmetros congelados que permitem reproduzir o cálculo depois.

    Lança:
        ErroSimulacao: se o aporte cair fora do prazo do contrato ou se não
            houver meses restantes para comparar.
    """
    if valor_aporte <= 0:
        raise ErroSimulacao("O valor do aporte deve ser positivo.")

    calculadora = criar_calculadora(financiamento.modelo)

    cronograma_base = calculadora.reconstruir_parcelas(financiamento, amortizacoes_atuais)
    if not cronograma_base:
        raise ErroSimulacao("O financiamento não possui parcelas a simular.")

    ultima_competencia = cronograma_base[-1].data_parcela
    meses_restantes = meses_entre(data_aporte, ultima_competencia)

    if meses_entre(financiamento.data_inicio, data_aporte) < 0:
        raise ErroSimulacao(
            "A data do aporte é anterior ao início do financiamento."
        )

    if meses_restantes <= 0:
        raise ErroSimulacao(
            "Na data informada o financiamento já estaria quitado — não há o que comparar."
        )

    saldo_na_data = next(
        (p.saldo_devedor for p in cronograma_base if p.data_parcela == data_aporte),
        None,
    )
    if saldo_na_data is not None and valor_aporte > saldo_na_data:
        raise ErroSimulacao(
            f"O aporte de R$ {valor_aporte:,.2f} supera o saldo devedor de "
            f"R$ {saldo_na_data:,.2f} na competência {data_aporte}."
        )

    # ── Cenário A: amortizar ────────────────────────────────────────────────
    aporte_simulado = AmortizacaoExtra(
        valor_amortizado=valor_aporte,
        data_amortizacao=data_aporte,
        tipo=tipo_amortizacao,
    )
    cronograma_novo = calculadora.reconstruir_parcelas(
        financiamento, [*amortizacoes_atuais, aporte_simulado]
    )

    base_por_mes = _parcelas_por_competencia(cronograma_base)
    novo_por_mes = _parcelas_por_competencia(cronograma_novo)

    # ── Parâmetros do investimento ──────────────────────────────────────────
    taxa_bruta = taxa_mensal_indicador * percentual_indicador
    ir = aliquota_ir(meses_restantes)

    montante_bruto = valor_aporte * (1 + taxa_bruta) ** meses_restantes
    rendimento_bruto = montante_bruto - valor_aporte
    montante_investir = valor_aporte + rendimento_bruto * (1 - ir)

    # Taxa líquida equivalente: é a ela que as parcelas economizadas rendem,
    # já que o dinheiro liberado seria aplicado no mesmo tipo de produto.
    if valor_aporte > 0 and meses_restantes > 0:
        taxa_liquida = (montante_investir / valor_aporte) ** (1 / meses_restantes) - 1
    else:
        taxa_liquida = 0.0

    # ── Cenário B: capitalizar as parcelas economizadas ─────────────────────
    montante_amortizar = 0.0
    juros_evitados = 0.0
    fluxo_economia = []

    competencias_futuras = sorted(
        c for c in set(base_por_mes) | set(novo_por_mes) if c > data_aporte
    )

    for competencia in competencias_futuras:
        economia_mes = base_por_mes.get(competencia, 0.0) - novo_por_mes.get(competencia, 0.0)
        if abs(economia_mes) < 1e-9:
            continue

        meses_ate_o_fim = meses_entre(competencia, ultima_competencia)
        montante_amortizar += economia_mes * (1 + taxa_liquida) ** meses_ate_o_fim
        juros_evitados += economia_mes

        fluxo_economia.append({
            'competencia': competencia,
            'economia': round(economia_mes, 2),
        })

    # A soma nominal das parcelas economizadas embute o próprio aporte;
    # o que sobra depois de descontá-lo são os juros que deixaram de correr.
    juros_evitados = max(juros_evitados - valor_aporte, 0.0)

    # ── Veredito ────────────────────────────────────────────────────────────
    diferenca = montante_amortizar - montante_investir
    limite_empate = valor_aporte * MARGEM_EMPATE

    if abs(diferenca) <= limite_empate:
        veredito = 'EMPATE'
    elif diferenca > 0:
        veredito = 'AMORTIZAR'
    else:
        veredito = 'INVESTIR'

    return {
        'veredito': veredito,
        'montante_amortizar': round(montante_amortizar, 2),
        'montante_investir': round(montante_investir, 2),
        'diferenca': round(abs(diferenca), 2),
        'juros_evitados': round(juros_evitados, 2),
        'valor_aporte': round(valor_aporte, 2),
        'data_aporte': data_aporte,
        'meses_restantes': meses_restantes,
        'taxa_mensal': round(taxa_mensal_indicador, 8),
        'percentual_indicador': percentual_indicador,
        'taxa_liquida_mensal': round(taxa_liquida, 8),
        'aliquota_ir': ir,
        'rendimento_bruto': round(rendimento_bruto, 2),
        'prazo_original': len(cronograma_base),
        'prazo_com_aporte': len(cronograma_novo),
        'mensagem': _monta_mensagem(veredito, abs(diferenca), percentual_indicador),
        'fluxo_economia': fluxo_economia,
    }


def _monta_mensagem(veredito: str, diferenca: float, percentual: float) -> str:
    """
    Redige a frase de resultado exibida na interface.

    Argumentos:
        veredito: 'AMORTIZAR', 'INVESTIR' ou 'EMPATE'.
        diferenca: diferença absoluta entre os montantes finais.
        percentual: fração do indicador usada na simulação.

    Retorna:
        Frase pronta para exibição, em português.
    """
    valor = f"R$ {diferenca:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    rendimento = f"{percentual * 100:.0f}% do indicador"

    if veredito == 'AMORTIZAR':
        return (
            f"Amortizar deixa você com {valor} a mais no fim do contrato "
            f"do que investir a {rendimento}."
        )
    if veredito == 'INVESTIR':
        return (
            f"Investir a {rendimento} deixa você com {valor} a mais no fim "
            f"do contrato do que amortizar."
        )
    return (
        "As duas estratégias terminam praticamente empatadas — a diferença "
        f"de {valor} está dentro da margem de 1% do aporte."
    )
