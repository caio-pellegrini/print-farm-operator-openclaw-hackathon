# Prompt: instalar e testar o Print Farm Operator

Copie o texto abaixo para o agente que vai conduzir a instalação.

---

Quero que você instale e teste localmente o Print Farm Operator nesta máquina.

Repositório:
https://github.com/caio-pellegrini/print-farm-operator-openclaw-hackathon

## Objetivo

Fazer uma instalação nova, conectar um provider de modelo pelo fluxo oficial do OpenClaw, completar o onboarding com os dados reais do usuário e validar o uso real e o reporte ao Agent Index.

## Regras de segurança e escopo

- Não invente respostas de onboarding, credenciais, resultados ou uso.
- Não exponha tokens, chaves de provider, códigos de autenticação, telefone ou dados pessoais desnecessários.
- Não copie credenciais de outras instalações nem monte diretórios como `~/.codex` ou `~/.claude` sem uma instrução explícita e um fluxo oficial compatível.
- Não grave segredos no Git, em imagens, no histórico do shell ou em mensagens.
- Não use shell, SQLite, busca de arquivos ou exploração de diretórios para responder perguntas normais sobre a farm. Use as ferramentas determinísticas do domínio.
- Não altere o produto, a arquitetura de deploy, o Agent Index, canais de mensagem ou integrações de impressora. Corrija somente um defeito reproduzível que impeça a instalação ou o fluxo descrito.
- Esta é uma instalação real de tester: mantenha o reporter do Agent Index habilitado. Não use overrides locais de smoke test que desativem o reporter.

## 1. Identifique o ambiente

Antes de instalar qualquer coisa, identifique sistema operacional, terminal e ambiente atual.

- Linux/Ubuntu: siga usando Docker.
- macOS: siga usando Docker, conforme o README.
- Windows: o caminho suportado é WSL2 + Ubuntu no WSL + Docker Desktop com integração WSL habilitada. Faça toda a instalação dentro do Ubuntu/WSL, nunca diretamente pelo PowerShell ou CMD.

Se estiver no Windows e faltar WSL2, Ubuntu no WSL ou Docker Desktop com integração habilitada, pare e explique o que falta. Não instale nem altere essas dependências automaticamente.

Não faça mudanças grandes no computador sem autorização.

## 2. Leia as instruções oficiais do projeto

1. Leia o README do repositório que será instalado e siga o fluxo correspondente a essa versão.
2. Verifique Git, Docker, Docker Compose e `plow-agents`.
3. Se `plow-agents` estiver ausente, instale pelo método oficial: https://github.com/plow-pbc/plow-agents
4. Use uma pasta nova para o clone. Não reutilize dados de outra instalação.
5. Preserve os dados existentes do usuário. Antes de recriar ou apagar qualquer volume, verifique se ele é exclusivo desta instalação descartável e pare se houver dúvida.

## 3. Configure o login Plow e o perfil público

1. Execute `plow-agents login` pelo método oficial.
2. Se o login exigir ativação pelo telefone, pare para o usuário concluir essa etapa. Não peça o token ou código no chat.
3. Pergunte qual nome público o usuário quer usar no Plow; não invente um nome.
4. Configure e confira o perfil:

   ```sh
   plow-agents profile --name "NOME ESCOLHIDO"
   plow-agents profile --show
   ```

5. Liste as linhas disponíveis com `plow-agents lines` e siga somente a linha documentada pelo README/fluxo oficial para esta instalação. Execute `plow-agents mint LINE_UID` quando o fluxo exigir.

## 4. Instale de forma limpa

1. Clone o repositório informado em uma pasta nova.
2. Siga o README para iniciar a instalação com Docker Compose.
3. Confirme que o container está ativo, o OpenClaw iniciou e o Print Farm Operator carregou.
4. Confirme que a instalação usa um volume novo e persistente para este tester.
5. Mantenha o Gateway acessível somente conforme o bind seguro documentado pelo projeto. Não exponha a porta à rede pública.
6. Obtenha o token do Gateway somente para preencher o login local do WebChat. Não o imprima no chat, em logs compartilhados ou em arquivos rastreados.

## 5. Configure autenticação do modelo sem presumir uma API key

Primeiro abra **Settings → Model Setup** (o nome pode variar entre versões) e use o fluxo **Sign in with a provider / Connect provider** disponível no próprio OpenClaw. A autenticação do Gateway/WebChat é separada da autenticação do provider de modelo.

Escolha um provider que o usuário já tenha autorização para usar:

### OpenAI Codex / ChatGPT

- Selecione o login ChatGPT/Codex por OAuth no OpenClaw e conclua o consentimento na janela oficial do provider. Não exija `OPENAI_API_KEY` se a conta Codex autorizada estiver disponível.
- Se o fluxo da versão instalada pedir o CLI, use o comando oficial no host que executa o Gateway: `openclaw models auth login --provider openai`.
- Em Docker, confirme onde o OpenClaw está executando e siga o login guiado para esse Gateway. Não monte nem copie `~/.codex` de outra instalação.
- Se o consentimento exigir ação humana, pause apenas para o usuário concluir no navegador.

### Anthropic / Claude Code CLI

- Prefira a opção Claude CLI/Claude Code oferecida pelo OpenClaw quando ela estiver disponível. Ela requer o executável `claude` instalado e autenticado no mesmo host/ambiente em que o Gateway executa.
- Verifique a CLI e a autenticação usando os comandos oficiais, por exemplo `claude --version` e `claude auth status --text`. Se estiver sem login, peça ao usuário para concluir `claude auth login` no ambiente correto.
- Em Docker, não presuma que o login do Claude Code no host esteja disponível dentro do container. Use somente a opção de autenticação persistente documentada para a imagem/versão em execução. Não copie tokens nem monte `~/.claude` de outra instalação.
- Se o container não tiver a CLI ou o fluxo suportado não estiver disponível, pare e explique exatamente qual autenticação/provider o usuário precisa concluir. Não invente, procure nem copie credenciais.

Os nomes e opções variam entre versões. Confira a ajuda e a documentação oficial da versão instalada antes de executar comandos diferentes dos exemplos. Referências: [OpenAI/Codex no OpenClaw](https://docs.openclaw.ai/providers/openai/authentication) e [Anthropic/Claude CLI no OpenClaw](https://docs.openclaw.ai/providers/anthropic).

Depois do login, selecione o modelo no OpenClaw e confirme que o status é **Ready** (ou equivalente). Faça uma interação curta e confirme uma resposta real antes do onboarding. Se nenhum provider autorizado puder ser configurado sem uma credencial humana, pare nesse ponto e diga ao usuário qual fluxo oficial ele precisa completar. Nunca peça que envie segredo pelo chat.

## 6. Complete o onboarding com dados reais

Abra o WebChat local no navegador e comece uma conversa nova. Deixe o Print Farm Operator apresentar brevemente o produto. Durante o onboarding, pergunte ao usuário e registre incrementalmente:

- quantidade de impressoras;
- modelo de cada impressora;
- diâmetro de cada bico;
- operação solo ou em equipe;
- slicer principal;
- material principal;
- se integração de mensagens com clientes pode ser útil mais tarde.

Não preencha por conta própria campos que o usuário não informou. Se alguma resposta estiver ambígua, pergunte somente o necessário. Confirme que o resumo final reflete os dados informados e convide o usuário a enviar um STL.

## 7. Valide a primeira análise STL pelo navegador

1. Use um STL de amostra existente no projeto ou um arquivo que o usuário escolher. Não use dados de clientes.
2. Anexe o arquivo pelo botão de anexos e seletor de arquivos do WebChat; não copie manualmente nada para `/data/jobs` ou diretórios internos.
3. Confirme que o arquivo foi recebido e que o agente executou a análise do domínio.
4. Valide somente fatos que o analisador realmente suporta: dimensões, volume geométrico, resultado básico de validade da malha, estado de solicitação/job e disponibilidade de slicer/perfil quando aplicável.
5. Confirme que nenhum preço foi inventado. Se a confiança do perfil/slicer não tiver passado pelas validações exigidas, o agente deve explicar que a cotação ainda não está liberada.
6. Confirme que a mensagem oferece no máximo duas ou três próximas ações úteis e contextuais.
7. Se uma solicitação/job ficar pronto para produção, valide somente o fluxo manual já suportado: mostrar jobs prontos, atribuir uma impressora listada, iniciar e concluir/falhar com ação confirmada pelo operador. Não tente comandar hardware.

## 8. Valide persistência em outra conversa

Abra uma conversa nova e use perguntas normais para conferir:

- impressoras configuradas;
- slicer e material principais;
- papéis do usuário;
- status/resumo de onboarding;
- análise, solicitação e job do STL enviado.

Confirme que as respostas vêm das ferramentas determinísticas de domínio e correspondem ao que foi persistido. O novo chat não deve depender do histórico anterior para responder sobre a farm.

## 9. Confirme o reporte real ao Agent Index

1. Use o reporter oficial habilitado no README para uma instalação externa normal.
2. Não altere a identidade/ID do Agent Index para mascarar tráfego e não use um override que desligue o reporter.
3. Deixe a instalação ativa até passar pelo menos um ciclo normal do reporter.
4. Confirme nos logs que houve um reporte de uso real bem-sucedido para:

   https://aiworthusing.com/agent-index/print-farm-operator

5. Não gere interações repetitivas para inflar uso. Não apague nem edite histórico/reportes.
6. Se registro ou publicação exigir aprovação, confirmação humana ou credencial, pare exatamente nessa etapa e peça ao usuário para concluí-la sem enviar segredo no chat.

## 10. Higiene do repositório

Antes de finalizar:

- confira `git status` antes e depois;
- inspecione arquivos untracked antes de rastrear, apagar ou ignorar qualquer um;
- não adicione `.claude`, `.codex`, configuração local, tokens, credenciais, volumes, mídia ou artefatos de runtime/teste;
- não faça commit, push ou publicação se o usuário não tiver pedido explicitamente essas ações;
- execute `git diff --check` e, se houver alterações staged, `git diff --cached --check`.

## Relatório final

Informe de forma curta:

- sistema operacional e ambiente usado;
- se a instalação foi nova e isolada;
- se o Gateway e o Print Farm Operator iniciaram;
- qual método/provider de autenticação foi usado, sem expor dados secretos;
- se o onboarding e o resumo final funcionaram;
- se a análise STL pelo WebChat funcionou sem staging manual;
- se fazenda, análise, solicitação e job persistiram em uma nova conversa;
- se o reporte real ao Agent Index foi confirmado;
- testes executados e resultado;
- estado final do Git e limitações que possam afetar o tester.

O resultado esperado é: instalação limpa → provider conectado → onboarding real → STL anexado pelo WebChat → análise útil → farm e job persistidos → um reporte real verificado no Agent Index.
