# Prompt: instalar e testar o Print Farm Operator

Use este roteiro para conduzir uma instalação limpa do Print Farm Operator
neste repositório Plow.

## Regras do ambiente

- Siga o README deste repositório. O runtime OpenClaw, Plow Chat, credenciais e
  rota padrão de modelo pertencem à base Plow.
- Não configure providers antigos, WhatsApp, outro runtime, nem copie estado ou
  credenciais de outra instalação.
- Mantenha o volume persistente exclusivo desta instalação. Não apague volumes
  sem identificar que pertencem somente ao smoke descartável.
- Não mostre tokens, chaves, dados pessoais, conteúdo de STL ou caminhos locais
  em logs compartilhados.
- Responda perguntas sobre a farm com as ferramentas de domínio. Não consulte
  SQLite ou arquivos diretamente.
- Impressoras são operadas manualmente. Nunca afirme que uma impressão começou
  ou terminou sem confirmação do operador.

## Instalação

1. Confirme que o checkout é `print-farm-operator-openclaw-hackathon` e leia o
   README.
2. Configure uma linha Plow pelo fluxo oficial `plow-agents login`, `lines` e
   `mint` descrito no README. Mantenha `plow-credentials` fora do Git.
3. Inicie com `docker compose up --build -d` e confira os logs do serviço
   `agent`. Use o dashboard local/WebChat em `http://localhost:3001` ou a linha
   Plow padrão.
4. Não faça setup separado de provider: a imagem Plow fornece sua rota normal
   de modelo. Se ela não estiver pronta, reporte o erro do runtime para o
   administrador Plow em vez de copiar credenciais de outro ambiente.

## Fluxo funcional

1. Em uma conversa nova, confirme que o agente se apresenta brevemente e oferece
   configurar a farm.
2. Informe dados reais: quantidade e modelos de impressora, bicos, operação
   solo/equipe, slicer e material principais e interesse futuro em mensagens a
   clientes. Verifique que as respostas são salvas uma por vez e que o agente
   resume a configuração persistida ao terminar.
3. Se a operação for solo, confirme que o principal recebe `OWNER` e
   `OPERATOR`. Pergunte pela configuração e valide a resposta usando o estado
   persistido.
4. Anexe um único STL de teste pelo seletor de arquivos do WebChat. O agente
   deve informar análise, dimensões disponíveis e os estados persistidos da
   solicitação e do job. A solicitação fica em rascunho; não há preço inventado
   nem cotação aprovada.
5. Abra uma conversa nova e pergunte pela configuração e pela última análise.
   Confirme que configuração, análise, solicitação e job foram recuperados.
6. Se houver uma solicitação pronta, exercite somente as ferramentas manuais de
   produção e confirme cada mudança com o operador.

## Verificações de desenvolvimento

No checkout do projeto, execute todos os testes Python e os comandos do plugin
documentados no `package.json`: `npm test` e `npm run plugin:validate` dentro
de `openclaw/plugins/print-farm-stl`. Antes de compartilhar alterações,
confirme `git diff --check` e que Dockerfile, Compose e configuração Plow não
foram substituídos por arquivos de outra instalação.

Para um smoke descartável, use um projeto Compose e volume próprios. Desative o
Agent Index reporter somente nesse ambiente isolado pelo mecanismo de override
suportado pela imagem Plow em uso; a instalação normal de tester mantém o
reporter herdado habilitado.
