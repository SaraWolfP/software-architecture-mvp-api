"""
cronograma.py
Persistência do cronograma de parcelas.

Sempre que o contrato ou suas amortizações mudam, o cronograma inteiro é
descartado e recalculado. É mais simples e mais seguro do que tentar corrigir
parcelas pontualmente, e o custo é irrelevante na escala deste sistema
(360 registros no pior caso).
"""

from __future__ import annotations

import banco_de_dados as bd
from servicos.calculadora import (
    amortizacoes_de_linhas,
    criar_calculadora,
    financiamento_de_linha,
)


def regravar_parcelas(conn, id_financiamento: int, linha_financiamento=None) -> int:
    """
    Recalcula e regrava todas as parcelas de um financiamento.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        id_financiamento: identificador do financiamento.
        linha_financiamento: linha já carregada da tabela Financiamentos.
            Se omitida, é buscada aqui.

    Retorna:
        Quantidade de parcelas gravadas.

    Lança:
        ValueError: se o financiamento não existir.
    """
    if linha_financiamento is None:
        resultado = bd.obtem_dados(conn, 'Financiamentos', coluna='id', valor=id_financiamento)
        if not resultado:
            raise ValueError("Financiamento não encontrado.")
        linha_financiamento = resultado[0]

    financiamento = financiamento_de_linha(linha_financiamento)

    linhas_amortizacoes = bd.obtem_dados(
        conn, 'AmortizacoesExtras',
        coluna='financiamento_id', valor=id_financiamento,
    )
    amortizacoes = amortizacoes_de_linhas(linhas_amortizacoes)

    calculadora = criar_calculadora(financiamento.modelo)
    parcelas = calculadora.reconstruir_parcelas(financiamento, amortizacoes)

    bd.deleta_dados(conn, 'Parcelas', coluna='financiamento_id', valor=id_financiamento)

    for parcela in parcelas:
        dados = parcela.to_dict()
        dados['financiamento_id'] = id_financiamento
        bd.insere_dado(conn, 'Parcelas', dados)

    return len(parcelas)


def marcar_simulacoes_obsoletas(conn, id_financiamento: int) -> int:
    """
    Marca como obsoletas as simulações de um financiamento que mudou.

    Uma simulação guarda montantes calculados sobre um cronograma específico.
    Se o contrato for editado, esses números deixam de ser reproduzíveis — em
    vez de apagá-los silenciosamente, o sistema os preserva sinalizados, para
    que a interface possa avisar o usuário.

    Argumentos:
        conn: conexão ativa com o banco de dados.
        id_financiamento: identificador do financiamento alterado.

    Retorna:
        Quantidade de simulações marcadas.
    """
    return bd.atualiza_dado(
        conn, 'Simulacoes', {'obsoleta': 1},
        coluna='financiamento_id', valor=id_financiamento,
    )
