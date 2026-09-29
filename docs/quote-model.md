# Modelo de orçamento

Protótipo executável: `python3 experiments/quote-engine/quote.py --weight-g 73 --hours 3.7 --material-brl-kg 90 --machine-brl-h 2 --margin .4 --quantity 4`.

## Componentes

`material = (g/1000 × quantidade × preço/kg)`

`máquina = horas × quantidade × custo_hora`

`energia = horas × quantidade × kWh_por_hora × tarifa_kWh`

`custo estimado = material + máquina + energia + setup`

`preço = custo / (1 - margem)`

Margem é fração sobre preço de venda (gross margin), não markup sobre custo. Ex.: custo R$ 10 e margem 40% → preço R$ 16,67.

## Objetivo vs parâmetros vs hipótese

- **Objetivo do slicer:** tempo estimado e material/filamento extrudado, condicionado ao perfil.
- **Parâmetros do negócio:** R$/kg, densidade, tarifa, energia/h, custo máquina/h, setup, margem, pós-processamento, falha/refugo e embalagem/frete.
- **Hipóteses iniciais do script:** PLA R$90/kg, máquina R$2/h, energia 0,12 kWh/h e tarifa R$0,95/kWh são exemplos configuráveis, não pesquisa de mercado nem valores objetivos.
- **Hipótese de quantidade:** custo/tempo escalam linearmente; na prática, peças podem caber juntas, compartilhar aquecimento e mudar ciclo.

Peso estimado pode ser obtido pelo comprimento total de filamento, diâmetro e densidade; preferir volume/length preciso reportado pelo slicer, e validar se inclui purge/prime tower. Para preço real o operador configura perdas e taxa de falha com histórico.

