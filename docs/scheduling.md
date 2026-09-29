# Scheduling simples

`experiments/farm-manager/farm.py` usa uma heurística *earliest compatible finish*:

1. Excluir máquinas offline.
2. Excluir máquinas com volume XYZ insuficiente (sem considerar rotação/packing) ou material incompatível.
3. Estimar disponibilidade atual por `free_at_h` (ocupação/fila manual ou telemetria).
4. Selecionar menor `free_at_h + job_duration`; se ninguém couber, deixar aguardando.

## Limitações

- Ignora prioridade, due date, cor/material disponível real, troca de bobina, falhas, refrigeração, limite de fila, batch de múltiplas peças e custo/qualidade.
- Dimensões atuais são alinhadas a XYZ, sem testar rotações/orientação nem envelope da cabeça.
- Saída recomendada não executa upload nem start.
- Com duração apenas estimada, horários são aproximações.

## Evolução com maior retorno

Registrar disponibilidade/estado real; validar filtro via slicer por printer profile; considerar prazo e setup-change penalty; manter motivo legível da recomendação; pedir confirmação humana. Aumentar complexidade só após telemetria real.

