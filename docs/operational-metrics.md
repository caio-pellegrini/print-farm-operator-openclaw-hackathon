# Métricas operacionais

Todos os valores exigem persistência e timestamp de eventos. A demo mock não é evidência sobre uma farm real.

| Métrica | Classe | Como calcular / dependência |
|---|---|---|
| Orçamentos/pedidos aceitos e fila | Medida no app | Eventos e estados locais. |
| Jobs concluídos/cancelados/falhos | Medida real com integração | Eventos de máquina + reconciliação; manualmente só status declarado pelo operador. |
| Horas de impressão | Medida real se host reportar | Início/fim de job OctoPrint/Moonraker; não contar só orçamento. |
| Material consumido | Estimativa de slicer; medição posterior possível | Soma do comprimento/volume informado e peso/densidade. Purge/refugo dependem do perfil. |
| Horas ocupadas / utilização | Derivada de estado observado | tempo printing / janela online observada; não dividir por tempo calendário se máquina estava offline. |
| Ociosidade | Estimada/medida | estado idle entre disponibilidade; exige telemetria contínua para fechar lacunas. |
| Receita | Real quando integrada ao pagamento/ERP; senão estimada | Soma de preço aceito/faturado, manter status explícito. |
| Custo/lucro/margem | Estimado até custos contábeis | orçamento por material/tempo/energia; custos de falha, trabalho humano, impostos e frete exigem configuração/histórico. |
| Atrasos/ETA | Estimativa | fila + estimativas do slicer + calendários; recalcular com andamento real. |
| Taxa de falhas e taxa de sucesso | Real com logs completos | falha reportada por máquina/operador e jobs concluídos, classificando motivo. |
| Consumo por material | Estimativa ou real | perfis/jobs e troca de bobina/peso registrado; sem sensor/cadastro, só estimado. |

POC mock: `experiments/farm-manager/farm.py` produz disponibilidade, recomendação e capacidade teórica. Não conectar ao relatório como métrica observada.

