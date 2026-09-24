"""
calculadora.py
Modelos de domínio e cálculo dos cronogramas de financiamento.

Dois sistemas de amortização são suportados:

    SAC    amortização constante, parcela decrescente.
    PRICE  parcela constante, amortização crescente.

E dois efeitos para uma amortização extraordinária:

    PARCELA  mantém o prazo e reduz o valor das parcelas seguintes.
    PRAZO    mantém o valor da parcela e antecipa a quitação.

Todo o cálculo é puro: nenhuma função aqui toca o banco de dados. Isso permite
que o comparador simule cenários hipotéticos sem persistir nada.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta

#: Saldo abaixo do qual o financiamento é considerado quitado (evita ruído de float).
TOLERANCIA_SALDO = 0.005


@dataclass
class Financiamento:
    """Dados contratuais de um financiamento imobiliário."""

    nome: str
    valor_imovel: float
    entrada: float
    taxa_juros: float
    prazo_meses: int
    data_inicio: str
    modelo: str

    @property
    def valor_financiado(self) -> float:
        """Montante efetivamente financiado, já descontada a entrada."""
        return self.valor_imovel - self.entrada

    def to_dict(self) -> dict:
        return {
            "nome": self.nome,
            "valor_imovel": self.valor_imovel,
            "entrada": self.entrada,
            "taxa_juros": self.taxa_juros,
            "prazo_meses": self.prazo_meses,
            "data_inicio": self.data_inicio,
            "modelo": self.modelo,
        }


@dataclass
class Parcela:
    """Uma parcela do cronograma, com a decomposição entre juros e amortização."""

    numero_parcela: int
    data_parcela: str
    valor_parcela: float
    juros: float = 0.0
    amortizacao: float = 0.0
    saldo_devedor: float = 0.0

    def to_dict(self) -> dict:
        return {
            "numero_parcela": self.numero_parcela,
            "data_parcela": self.data_parcela,
            "valor_parcela": self.valor_parcela,
            "juros": self.juros,
            "amortizacao": self.amortizacao,
            "saldo_devedor": self.saldo_devedor,
        }


@dataclass
class AmortizacaoExtra:
    """Um pagamento extraordinário aplicado fora do cronograma regular."""

    valor_amortizado: float
    data_amortizacao: str
    tipo: str

    def to_dict(self) -> dict:
        return {
            "valor_amortizado": self.valor_amortizado,
            "data_amortizacao": self.data_amortizacao,
            "tipo": self.tipo,
        }


def _data_para_date(ano_mes: str) -> date:
    """
    Converte 'YYYY-MM' em um objeto date no primeiro dia do mês.

    Argumentos:
        ano_mes: competência no formato 'YYYY-MM'.

    Retorna:
        date correspondente ao dia 1 daquele mês.
    """
    ano, mes = map(int, ano_mes.split("-"))
    return date(ano, mes, 1)


def meses_entre(inicio: str, fim: str) -> int:
    """
    Conta quantos meses separam duas competências.

    Argumentos:
        inicio: competência inicial, 'YYYY-MM'.
        fim: competência final, 'YYYY-MM'.

    Retorna:
        Diferença em meses (negativa se `fim` for anterior a `inicio`).
    """
    d_inicio, d_fim = _data_para_date(inicio), _data_para_date(fim)
    return (d_fim.year - d_inicio.year) * 12 + (d_fim.month - d_inicio.month)


class Calculadora(ABC):
    """
    Contrato comum aos sistemas de amortização.

    As subclasses diferem apenas em como determinam, a cada mês, quanto do
    pagamento é amortização e quanto é juros. Todo o resto do cronograma —
    a iteração pelos meses, a aplicação das amortizações extras e o ajuste
    da última parcela — vive em `reconstruir_parcelas`.
    """

    #: Rótulo do modelo, usado por `criar_calculadora`.
    modelo: str = ''

    @abstractmethod
    def _amortizacao_do_mes(self, saldo: float, taxa: float, parcelas_restantes: int) -> float:
        """
        Calcula quanto do pagamento do mês abate o principal.

        Argumentos:
            saldo: saldo devedor no início do mês.
            taxa: taxa de juros mensal em decimal.
            parcelas_restantes: quantas parcelas ainda faltam, incluindo esta.

        Retorna:
            Parcela de amortização do principal, antes do ajuste de quitação.
        """

    @abstractmethod
    def _valor_a_travar(self, amortizacao: float, valor_parcela: float) -> float:
        """
        Escolhe qual grandeza permanece fixa após uma amortização do tipo PRAZO.

        Cada sistema congela uma coisa diferente, e trocar uma pela outra produz
        um cronograma errado: no SAC o que se mantém é a parcela de principal;
        no PRICE, o pagamento total.

        Argumentos:
            amortizacao: parcela de principal do mês do aporte.
            valor_parcela: pagamento total do mês do aporte.

        Retorna:
            Valor a ser memorizado para os meses seguintes.
        """

    @abstractmethod
    def _amortizacao_travada(self, valor_travado: float, juros: float) -> float:
        """
        Reconstrói a amortização do mês a partir da grandeza congelada.

        Argumentos:
            valor_travado: valor devolvido por `_valor_a_travar`.
            juros: juros do mês corrente.

        Retorna:
            Parcela de amortização do principal.
        """

    @abstractmethod
    def _meses_para_quitar(self, saldo: float, taxa: float, valor_travado: float) -> int:
        """
        Estima em quantos meses o saldo se esgota mantendo a grandeza congelada.

        Usado depois de uma amortização do tipo PRAZO para encurtar o prazo
        efetivo do contrato. Sem isso, uma amortização PARCELA lançada mais
        tarde re-espalharia o saldo pelo prazo *original*, desfazendo em
        silêncio o encurtamento que a PRAZO havia produzido.

        Argumentos:
            saldo: saldo devedor após o aporte.
            taxa: taxa de juros mensal em decimal.
            valor_travado: valor devolvido por `_valor_a_travar`.

        Retorna:
            Quantidade de meses, arredondada para cima.
        """

    def calcular_parcelas(
        self,
        valor_financiado: float,
        data_inicio: str,
        prazo_meses: int,
        taxa_juros: float,
    ) -> list[Parcela]:
        """
        Calcula o cronograma original, sem nenhuma amortização extraordinária.

        Argumentos:
            valor_financiado: saldo devedor inicial.
            data_inicio: competência da primeira parcela, 'YYYY-MM'.
            prazo_meses: número total de parcelas.
            taxa_juros: taxa mensal em decimal (0.0089 = 0,89% a.m.).

        Retorna:
            Lista de Parcela em ordem cronológica.
        """
        financiamento = Financiamento(
            nome='', valor_imovel=valor_financiado, entrada=0,
            taxa_juros=taxa_juros, prazo_meses=prazo_meses,
            data_inicio=data_inicio, modelo=self.modelo,
        )
        return self.reconstruir_parcelas(financiamento, [])

    def reconstruir_parcelas(
        self,
        financiamento: 'Financiamento',
        amortizacoes: list['AmortizacaoExtra'],
    ) -> list[Parcela]:
        """
        Constrói o cronograma completo aplicando todas as amortizações extras.

        A amortização extra lançada na competência M produz efeito a partir de
        M+1: a parcela de M já estava em curso quando o aporte foi feito.

        Efeito por tipo:
            PARCELA  recalcula o pagamento para o prazo remanescente — o número
                     de parcelas não muda, o valor de cada uma cai.
            PRAZO    mantém o pagamento e deixa o saldo se esgotar antes — o
                     valor não muda, o cronograma encurta.

        Argumentos:
            financiamento: dados contratuais originais.
            amortizacoes: lista de AmortizacaoExtra a aplicar (pode ser vazia).

        Retorna:
            Lista de Parcela já com todas as amortizações refletidas.
        """
        taxa = financiamento.taxa_juros
        saldo = financiamento.valor_financiado
        prazo = financiamento.prazo_meses

        data_atual = _data_para_date(financiamento.data_inicio)
        ordenadas = sorted(amortizacoes, key=lambda a: a.data_amortizacao)

        #: Grandeza congelada por uma amortização do tipo PRAZO — no SAC é a
        #: parcela de principal; no PRICE, o pagamento total.
        valor_travado: float | None = None

        parcelas: list[Parcela] = []
        numero = 1
        indice_amortizacao = 0

        while numero <= prazo and saldo > TOLERANCIA_SALDO:
            competencia = data_atual.strftime("%Y-%m")
            restantes = prazo - numero + 1

            juros = saldo * taxa

            if valor_travado is not None:
                amortizacao = self._amortizacao_travada(valor_travado, juros)
            else:
                amortizacao = self._amortizacao_do_mes(saldo, taxa, restantes)

            # Última parcela: amortiza exatamente o que sobrou, sem passar do saldo.
            if amortizacao >= saldo or restantes == 1:
                amortizacao = saldo

            valor_parcela = amortizacao + juros
            saldo -= amortizacao

            # A parcela é derivada dos componentes já arredondados, e não
            # arredondada por conta própria: assim a identidade
            # `parcela = juros + amortização` vale exatamente na resposta da API.
            juros_exibido = round(juros, 2)
            amortizacao_exibida = round(amortizacao, 2)

            parcelas.append(Parcela(
                numero_parcela=numero,
                data_parcela=competencia,
                valor_parcela=round(juros_exibido + amortizacao_exibida, 2),
                juros=juros_exibido,
                amortizacao=amortizacao_exibida,
                saldo_devedor=round(max(saldo, 0.0), 2),
            ))

            data_atual += relativedelta(months=1)
            numero += 1

            # Amortizações lançadas nesta competência: efeito a partir do mês seguinte.
            while (indice_amortizacao < len(ordenadas)
                   and ordenadas[indice_amortizacao].data_amortizacao == competencia):
                extra = ordenadas[indice_amortizacao]
                saldo -= extra.valor_amortizado
                indice_amortizacao += 1

                if saldo <= TOLERANCIA_SALDO:
                    # O aporte quitou o contrato. A última parcela emitida foi a
                    # do mês do aporte, e o saldo que ela carrega já não existe.
                    saldo = 0.0
                    parcelas[-1].saldo_devedor = 0.0
                    break

                parcelas[-1].saldo_devedor = round(saldo, 2)

                if extra.tipo == 'PRAZO':
                    # Congela a grandeza que o sistema mantém fixa; o prazo é que cede.
                    valor_travado = self._valor_a_travar(amortizacao, valor_parcela)

                    # O encurtamento precisa ficar registrado no prazo efetivo.
                    # `numero` já aponta para o próximo mês, então os meses já
                    # emitidos são `numero - 1`. Se mais tarde vier uma PARCELA,
                    # ela redistribui o saldo sobre este prazo encurtado — e não
                    # sobre o original, o que anularia o efeito desta.
                    meses = self._meses_para_quitar(saldo, taxa, valor_travado)
                    prazo = min(prazo, (numero - 1) + meses)
                else:
                    # Tipo PARCELA: volta a recalcular o pagamento a cada mês.
                    valor_travado = None

        return parcelas


class CalculadoraSac(Calculadora):
    """
    Sistema de Amortização Constante.

    A parcela de principal é a mesma todo mês; como os juros incidem sobre um
    saldo que só diminui, o pagamento total cai ao longo do contrato.
    """

    modelo = 'SAC'

    def _amortizacao_do_mes(self, saldo: float, taxa: float, parcelas_restantes: int) -> float:
        if parcelas_restantes <= 0:
            return saldo
        return saldo / parcelas_restantes

    def _valor_a_travar(self, amortizacao: float, valor_parcela: float) -> float:
        # No SAC quem se mantém constante é a parcela de principal. Congelar o
        # pagamento total encurtaria o contrato muito além do correto, porque a
        # parcela do SAC é decrescente por natureza.
        return amortizacao

    def _amortizacao_travada(self, valor_travado: float, juros: float) -> float:
        return valor_travado

    def _meses_para_quitar(self, saldo: float, taxa: float, valor_travado: float) -> int:
        # Com a amortização de principal fixa, o saldo cai linearmente.
        if valor_travado <= 0:
            return 0
        return max(1, math.ceil(saldo / valor_travado - 1e-9))


class CalculadoraPrice(Calculadora):
    """
    Sistema Francês de Amortização (Tabela PRICE).

    O pagamento total é constante. No início ele é quase todo juros; conforme o
    saldo cai, a fatia de amortização cresce.

        PMT = S · i / (1 − (1 + i)^−n)
    """

    modelo = 'PRICE'

    def _amortizacao_do_mes(self, saldo: float, taxa: float, parcelas_restantes: int) -> float:
        if parcelas_restantes <= 0:
            return saldo

        if taxa <= 0:
            return saldo / parcelas_restantes

        fator = (1 + taxa) ** -parcelas_restantes
        pagamento = saldo * taxa / (1 - fator)
        return pagamento - saldo * taxa

    def _valor_a_travar(self, amortizacao: float, valor_parcela: float) -> float:
        # No PRICE a grandeza constante é o pagamento total (PMT).
        return valor_parcela

    def _amortizacao_travada(self, valor_travado: float, juros: float) -> float:
        return valor_travado - juros

    def _meses_para_quitar(self, saldo: float, taxa: float, valor_travado: float) -> int:
        # Inversão da fórmula do PMT para o número de períodos:
        #   n = ln(PMT / (PMT − S·i)) / ln(1 + i)
        if taxa <= 0:
            return max(1, math.ceil(saldo / valor_travado - 1e-9)) if valor_travado > 0 else 0

        juros_do_saldo = saldo * taxa
        if valor_travado <= juros_do_saldo:
            # O pagamento não cobre nem os juros: o saldo nunca se esgotaria.
            # Devolve um valor grande para que o `min` no chamador não encurte.
            return 10 ** 6

        n = math.log(valor_travado / (valor_travado - juros_do_saldo)) / math.log(1 + taxa)
        return max(1, math.ceil(n - 1e-9))


#: Modelos disponíveis, na ordem em que aparecem na interface.
MODELOS_SUPORTADOS = ('SAC', 'PRICE')

#: Efeitos possíveis de uma amortização extraordinária.
TIPOS_AMORTIZACAO = ('PARCELA', 'PRAZO')


def criar_calculadora(modelo: str) -> Calculadora:
    """
    Instancia a calculadora correspondente ao modelo informado.

    Argumentos:
        modelo: 'SAC' ou 'PRICE'.

    Retorna:
        Instância de Calculadora.

    Lança:
        ValueError: se o modelo não for reconhecido.
    """
    if modelo == 'SAC':
        return CalculadoraSac()
    if modelo == 'PRICE':
        return CalculadoraPrice()
    raise ValueError(f"Modelo '{modelo}' não suportado. Use um de {MODELOS_SUPORTADOS}.")


def financiamento_de_linha(linha) -> Financiamento:
    """
    Converte uma linha da tabela Financiamentos em objeto de domínio.

    Argumentos:
        linha: sqlite3.Row ou dicionário com as colunas do financiamento.

    Retorna:
        Instância de Financiamento.
    """
    return Financiamento(
        nome=linha['nome'],
        valor_imovel=linha['valor_imovel'],
        entrada=linha['entrada'],
        taxa_juros=linha['taxa_juros'],
        prazo_meses=linha['prazo_meses'],
        data_inicio=linha['data_inicio'],
        modelo=linha['modelo'],
    )


def amortizacoes_de_linhas(linhas) -> list[AmortizacaoExtra]:
    """
    Converte linhas da tabela AmortizacoesExtras em objetos de domínio.

    Argumentos:
        linhas: iterável de sqlite3.Row ou dicionários.

    Retorna:
        Lista de AmortizacaoExtra.
    """
    return [
        AmortizacaoExtra(
            valor_amortizado=linha['valor_amortizado'],
            data_amortizacao=linha['data_amortizacao'],
            tipo=linha['tipo'],
        )
        for linha in linhas
    ]
