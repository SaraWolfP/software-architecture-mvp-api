# Amortiza ou Investe? — API

API REST que calcula cronogramas de financiamento imobiliário pelos sistemas **SAC** e **PRICE**, simula amortizações extraordinárias e compara a decisão de **amortizar a dívida** com a de **investir o mesmo valor** à taxa de mercado — usando as séries do Banco Central do Brasil.

Esta é a **componente secundária** do sistema. A interface que a consome está em:
`https://github.com/SaraWolfP/software-architecture-mvp-front`

---

## O que esta API faz de diferente

Qualquer calculadora de financiamento monta um cronograma. O que esta API acrescenta é a resposta a uma pergunta de decisão:

> Sobrou dinheiro. Amortizar ou investir?

Para responder, ela consome o **SGS do Banco Central**, converte as séries para uma base comum, aplica o Imposto de Renda regressivo e devolve as duas estratégias como montantes diretamente comparáveis na mesma data.

---

## Arquitetura

![Arquitetura da aplicação](docs/arquitetura.png)

Esta API ocupa a caixa do meio: recebe requisições da interface, persiste em SQLite e consome a componente externa.

---

## Tecnologias

- **Python 3.11**
- [Flask 3.0](https://flask.palletsprojects.com/) — roteamento e serialização
- [Gunicorn](https://gunicorn.org/) — servidor WSGI de produção
- **SQLite 3** — persistência, sem servidor externo
- [Flasgger](https://github.com/flasgger/flasgger) — documentação OpenAPI em `/apidocs`
- [Requests](https://requests.readthedocs.io/) — cliente HTTP do Banco Central
- [python-dateutil](https://dateutil.readthedocs.io/) — aritmética de competências
- [pytest](https://docs.pytest.org/) — 77 testes automatizados

---

## Estrutura do projeto

```
software-architecture-mvp-api/
├── app.py                        # Ponto de entrada: blueprints, Swagger, /health
├── banco_de_dados.py             # Conexão SQLite e operações genéricas
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── rotas/
│   ├── financiamento.py          # CRUD do contrato (GET, POST, PUT, DELETE)
│   ├── parcelas.py               # Consulta do cronograma
│   ├── amortizacoes.py           # Amortizações extraordinárias
│   ├── indicadores.py            # Dados da componente externa
│   └── simulacoes.py             # Comparação amortizar vs. investir
├── servicos/
│   ├── calculadora.py            # Cronogramas SAC e PRICE (orientado a objetos)
│   ├── comparador.py             # Regra de negócio da comparação
│   ├── cliente_bcb.py            # Cliente HTTP do SGS/Banco Central
│   ├── cache_indicadores.py      # Cache local com TTL e fallback
│   ├── cronograma.py             # Regravação das parcelas
│   └── validacao.py              # Validadores compartilhados pelas rotas
├── testes/
│   ├── test_calculadora.py       # Cronogramas SAC e PRICE
│   ├── test_comparador.py        # Regra financeira e faixas de IR
│   └── test_rotas.py             # Integração das 19 rotas
└── docs/
    └── arquitetura.png
```

Convenções: `snake_case` para arquivos, funções e variáveis Python; `CamelCase` para classes e nomes de tabelas.

---

## Modelo de dados

```
Financiamentos            Parcelas                    AmortizacoesExtras
──────────────            ────────                    ──────────────────
id (PK)                   id (PK)                     id (PK)
nome (UNIQUE)             financiamento_id (FK) ←     financiamento_id (FK) ←
valor_imovel              numero_parcela              valor_amortizado
entrada                   data_parcela                data_amortizacao
taxa_juros                valor_parcela               tipo (PARCELA|PRAZO)
prazo_meses               juros                       UNIQUE(fin_id, data)
data_inicio               amortizacao
modelo (SAC|PRICE)        saldo_devedor

IndicadoresCache                      Simulacoes
────────────────                      ──────────
id (PK)                               id (PK)
codigo_serie                          financiamento_id (FK) ←
nome_indicador (CDI|SELIC|IPCA)       valor_aporte · data_aporte
data_referencia                       indicador · percentual_indicador
valor                                 taxa_mensal · aliquota_ir
atualizado_em                         meses_restantes
UNIQUE(codigo_serie, data_ref)        montante_amortizar · montante_investir
                                      juros_evitados · diferenca
                                      veredito · obsoleta · criada_em
```

**Deleção em cascata:** apagar um financiamento remove suas parcelas, amortizações e simulações.

**Por que a taxa é congelada em `Simulacoes`:** uma simulação salva precisa ser reproduzível. Se a taxa fosse relida a cada consulta, o resultado guardado hoje mostraria números diferentes amanhã.

**Por que existe `obsoleta`:** editar o contrato invalida os montantes calculados sobre o cronograma anterior. Em vez de apagar o histórico em silêncio, o registro é preservado e sinalizado.

---

## Rotas

Base: `http://localhost:5000` · Documentação interativa: `/apidocs`

### Financiamentos

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/financiamento/` | Cria o contrato e calcula todo o cronograma |
| `GET` | `/financiamento/` | Lista com filtro, ordenação e paginação |
| `GET` | `/financiamento/<id>` | Detalha um contrato |
| `PUT` | `/financiamento/<id>` | Edita e reconstrói o cronograma |
| `DELETE` | `/financiamento/<id>` | Remove em cascata |

Parâmetros de consulta da listagem:

```
GET /financiamento/?modelo=SAC&ordenar_por=valor_imovel&ordem=desc&pagina=1&por_pagina=10
```

```json
{
  "itens": [ ... ],
  "paginacao": { "pagina": 1, "por_pagina": 10, "total": 23, "total_paginas": 3 }
}
```

> `ordenar_por` é validado contra uma allowlist de colunas. Como o nome da coluna é interpolado na instrução SQL, aceitar texto livre ali abriria uma injeção.

### Parcelas

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/financiamento/<id>/parcelas` | Cronograma completo; aceita `?ano=2027` e `?agrupar=ano` |
| `GET` | `/financiamento/<id>/parcelas/resumo` | Totais de juros, amortização e desembolso |

As parcelas são somente-leitura por design: nascem do contrato e das amortizações, e são regravadas automaticamente sempre que um dos dois muda.

### Amortizações extraordinárias

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/financiamento/<id>/amortizacoes` | Registra e recalcula |
| `GET` | `/financiamento/<id>/amortizacoes` | Lista; aceita `?tipo=PARCELA` |
| `PUT` | `/financiamento/<id>/amortizacoes/<id_amort>` | Altera e reconstrói |
| `DELETE` | `/financiamento/<id>/amortizacoes/<id_amort>` | Remove e reconstrói |

Dois efeitos possíveis:

- **`PARCELA`** — mantém o prazo e reduz o valor das parcelas seguintes
- **`PRAZO`** — mantém o valor e antecipa a quitação

> O que fica congelado no efeito `PRAZO` depende do sistema: no **SAC** é a parcela de principal, no **PRICE** é o pagamento total. Trocar um pelo outro produz um cronograma errado — no SAC, congelar o pagamento total encurtaria o contrato muito além do devido, porque a parcela do SAC já é decrescente por natureza.

### Indicadores — componente externa

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/indicadores/` | CDI, Selic e IPCA correntes, com histórico |
| `GET` | `/indicadores/<nome>/historico` | Série de um indicador; aceita `?ultimos=24` |
| `POST` | `/indicadores/atualizar` | Força a releitura no BCB, ignorando o cache |

### Simulações — amortizar vs. investir

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/financiamento/<id>/simulacoes` | Compara as estratégias e salva |
| `GET` | `/financiamento/<id>/simulacoes` | Histórico; aceita `?veredito=AMORTIZAR` |
| `PUT` | `/financiamento/<id>/simulacoes/<id_sim>` | Recalcula com novos parâmetros |
| `DELETE` | `/financiamento/<id>/simulacoes/<id_sim>` | Remove do histórico |

### Infraestrutura

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Liveness. Responde **200 sempre** |
| `GET` | `/apidocs` | Documentação Swagger |

> `/health` informa o estado do BCB no corpo (`{"bcb": "ok"\|"indisponivel"}`), **nunca** no código HTTP. Se o healthcheck do container dependesse da componente externa, uma instabilidade do Banco Central deixaria o container *unhealthy* e a interface sem subir — por um motivo alheio a esta aplicação.

**Total: 19 rotas**, cobrindo `GET`, `POST`, `PUT` e `DELETE`.

---

## A componente externa: SGS — Banco Central do Brasil

| Item | Detalhe |
|---|---|
| **Provedor** | Banco Central do Brasil |
| **URL base** | `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados` |
| **Cadastro** | Não é necessário — sem chave, sem token |
| **Custo** | Gratuito |
| **Licença** | Dados públicos (Lei 12.527/2011 e Decreto 8.777/2016); pede-se apenas a citação da fonte |
| **Documentação** | https://dadosabertos.bcb.gov.br/ |

### Séries consumidas

| Série | Código | Unidade devolvida | Uso |
|---|---|---|---|
| CDI acumulado no mês | 4390 | % ao mês | Rendimento do cenário "investir" |
| Selic acumulada no mês, anualizada | 4189 | % ao ano (base 252) | Taxa alternativa e referência de mercado |
| IPCA — variação mensal | 433 | % ao mês | Exibição informativa |

### Tratamento aplicado

O módulo `servicos/cliente_bcb.py` é a única parte do sistema que fala com a internet. Ele traduz:

```
BCB    {"data": "01/09/2026", "valor": "0.41"}
Aqui   {"data_referencia": "2026-09", "valor": 0.0041}
```

E normaliza as unidades. A Selic vem anualizada; o CDI e o IPCA, mensais:

```python
taxa_mensal = (1 + taxa_anual) ** (1/12) - 1   # 15% a.a. → 1,1715% a.m.
```

O módulo `servicos/cache_indicadores.py` guarda as séries no SQLite com TTL configurável. A ordem de tentativa é: cache fresco → BCB → cache vencido. Toda resposta carrega o campo `origem`, para que a interface possa avisar quando o dado não é o mais recente.

---

## Como executar

### Opção 1 — Docker (recomendado)

```bash
git clone https://github.com/SaraWolfP/software-architecture-mvp-api.git
cd software-architecture-mvp-api

docker build -t simulador-api .
docker run --rm -p 5000:5000 -v dados-api:/app/dados simulador-api
```

Acesse a documentação em http://localhost:5000/apidocs

> Para subir **API + interface juntas**, use o `docker compose up` do repositório da interface — é lá que fica o arquivo de composição.

### Opção 2 — Ambiente local

**Pré-requisito:** Python 3.9 ou superior.

```bash
# 1. Clone o repositório
git clone https://github.com/SaraWolfP/software-architecture-mvp-api.git
cd software-architecture-mvp-api

# 2. Crie e ative um ambiente virtual
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Suba o servidor
python app.py
```

A API sobe em `http://127.0.0.1:5000` e o banco é criado automaticamente na primeira execução.

> Se você já rodou uma versão anterior deste projeto, apague o `banco_de_dados.db` antigo antes de subir: o esquema ganhou colunas e tabelas novas, e `CREATE TABLE IF NOT EXISTS` não migra o que já existe.

### Variáveis de ambiente

| Variável | Padrão | Função |
|---|---|---|
| `DB_PATH` | `banco_de_dados.db` | Caminho do arquivo SQLite |
| `DB_TIMEOUT` | `10` | Segundos de espera por uma escrita |
| `BCB_TIMEOUT` | `10` | Segundos de espera pelo Banco Central |
| `CACHE_TTL_HORAS` | `6` | Validade do cache de indicadores |
| `FLASK_HOST` | `127.0.0.1` | Interface de escuta (só em `python app.py`) |
| `FLASK_PORT` | `5000` | Porta |
| `FLASK_DEBUG` | `1` | Recarga automática em desenvolvimento |

---

## Testes

```bash
pip install pytest
python -m pytest testes/ -v
```

São 77 testes, organizados em três arquivos:

- **`test_calculadora.py`** — identidades contábeis dos cronogramas: a soma das amortizações fecha com o valor financiado, o saldo zera na última parcela, cada parcela é exatamente juros mais amortização.
- **`test_comparador.py`** — regra financeira: faixas do IR por dias corridos, ausência de dupla contagem do principal, e existência de um único ponto de indiferença conforme a taxa sobe.
- **`test_rotas.py`** — integração das 19 rotas contra um banco temporário, com a componente externa simulada para que a suíte não dependa da internet.

Os testes da regra financeira não são cerimônia: é a única parte do sistema onde um erro sai plausível. Dois números grandes e um veredito convincente passam despercebidos mesmo quando a fórmula está errada.

---

## Como a comparação é calculada

O erro comum ao responder "amortizar ou investir" é somar as parcelas economizadas e comparar com o rendimento do investimento. Isso é inválido por dois motivos:

1. A diferença entre os cronogramas **contém o próprio aporte** — o dinheiro sai das parcelas futuras —, enquanto o rendimento não contém o principal.
2. Parcelas espalhadas por 20 anos e um montante resgatado no fim **não estão no mesmo instante do tempo**.

A formulação usada aqui coloca as duas estratégias no mesmo ponto de partida (R$ *A* na competência do aporte) e no mesmo ponto de chegada (a última parcela do contrato original):

| Estratégia | O que acontece | Montante final |
|---|---|---|
| **Amortizar** | Gasta *A* abatendo o saldo; a sobra mensal é aplicada | `Σ (parcela_orig − parcela_nova) × (1 + taxa_líq)^(meses até o fim)` |
| **Investir** | Aplica *A* pelo horizonte; paga IR no resgate | `A + [A × (1 + taxa × %)^n − A] × (1 − IR)` |

Alíquotas do IR sobre renda fixa, por **dias corridos**: 22,5% até 180, 20% até 360, 17,5% até 720, 15% acima.

Diferenças abaixo de 1% do aporte são reportadas como empate técnico.

---

## Exemplos de uso

```bash
BASE=http://localhost:5000

# Criar um financiamento
curl -X POST $BASE/financiamento/ \
  -H 'Content-Type: application/json' \
  -d '{"nome":"Apto Botafogo","valor_imovel":500000,"entrada":100000,
       "taxa_juros":0.89,"prazo_meses":360,"data_inicio":"2026-01","modelo":"SAC"}'

# Listar ordenando pelo valor do imóvel
curl "$BASE/financiamento/?ordenar_por=valor_imovel&ordem=desc&por_pagina=5"

# Editar o contrato (recalcula o cronograma inteiro)
curl -X PUT $BASE/financiamento/1 \
  -H 'Content-Type: application/json' \
  -d '{"nome":"Apto Botafogo","valor_imovel":520000,"entrada":120000,
       "taxa_juros":0.82,"prazo_meses":300,"data_inicio":"2026-01","modelo":"PRICE"}'

# Ver os indicadores vindos do Banco Central
curl "$BASE/indicadores/"

# Comparar amortizar com investir a 110% do CDI
curl -X POST $BASE/financiamento/1/simulacoes \
  -H 'Content-Type: application/json' \
  -d '{"valor_aporte":50000,"data_aporte":"2027-06",
       "indicador":"CDI","percentual_indicador":110}'

# Lançar a amortização de fato, reduzindo o prazo
curl -X POST $BASE/financiamento/1/amortizacoes \
  -H 'Content-Type: application/json' \
  -d '{"valor_amortizado":50000,"data_amortizacao":"2027-06","tipo":"PRAZO"}'

# Remover
curl -X DELETE $BASE/financiamento/1
```

---

## Decisões de implementação

**Um worker, várias threads.** O `CMD` do Dockerfile usa `--workers 1 --threads 4`. Dois processos escrevendo no mesmo arquivo SQLite produzem `database is locked`; o modo WAL e o `timeout` da conexão cobrem a concorrência entre threads.

**`inicializa_db()` no escopo de módulo.** Dentro de `if __name__ == '__main__'` o Gunicorn nunca executaria essa chamada, e o banco jamais seria criado no container.

**`DB_PATH` por variável de ambiente.** Sem isso o banco vive na camada da imagem e some a cada recriação do container.

**Cálculo puro, separado da persistência.** Nada em `servicos/calculadora.py` toca o banco, o que permite ao comparador simular cenários hipotéticos sem gravar nada.

**Cronograma regravado por inteiro.** Toda mudança descarta e recalcula as parcelas, em vez de corrigi-las pontualmente. É mais simples, elimina uma classe inteira de bugs de estado inconsistente, e o custo é irrelevante na escala do sistema — 360 registros no pior caso.

---

## Limitações conhecidas

- A projeção assume a taxa do indicador constante ao longo do horizonte. É uma referência de decisão, não uma previsão.
- Não modela seguros obrigatórios (MIP/DFI), taxa de administração nem correção do saldo por TR.
- SQLite é adequado a um MVP de usuário único; um cenário multiusuário pediria PostgreSQL.
- **Exercício acadêmico — não constitui recomendação de investimento.**

---

## Repositórios do projeto

| Componente | Repositório |
|---|---|
| Interface (principal) | https://github.com/SaraWolfP/software-architecture-mvp-front |
| API REST (secundária) | https://github.com/SaraWolfP/software-architecture-mvp-api |
| API externa consumida | https://api.bcb.gov.br/dados/serie/bcdata.sgs.4390/dados?formato=json |

---

## Autoria

Desenvolvido por **Sara Wolf Peretti** como MVP da disciplina de Arquitetura de Software — Pós-graduação em Engenharia de Software, PUC-Rio.

Fonte dos dados econômicos: Banco Central do Brasil, Sistema Gerenciador de Séries Temporais (SGS).
