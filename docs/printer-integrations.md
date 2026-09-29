# Integrações com impressoras

Pesquisa documental em fontes de fabricante/projeto; nenhum hardware foi conectado. As operações abaixo são capacidade documentada, não teste local.

| Ecossistema | API/estado | Ações documentadas | Esforço/abrangência para MVP |
|---|---|---|---|
| OctoPrint | 🟡 Investigado via API oficial REST v1; API key/header ou Application Keys Plugin. GET job documenta state, progress, print time, printTimeLeft, temps/filament conforme endpoint | upload/select/start, pause/resume/cancel; G-code | REST simples. **Inferência:** bom alcance para MVP porque várias máquinas podem ser anexadas a OctoPrint; cada máquina requer OctoPrint configurado. Melhor candidato observado, não testado em hardware. |
| Klipper/Moonraker | 🟡 Docs oficiais REST/WebSocket local: printer info/objects e status; auth via config/trusted clients | upload/start/pause/resume/cancel, gcode/script; macros | Boa opção para instalação Klipper; cada farm/máquina precisa de Moonraker e auth de LAN adequada. Nenhuma chamada executada.
| PrusaLink / Prusa Connect | 🟡 Docs oficiais confirmam acesso local PrusaLink e cloud Connect, telemetria/status e envio/controle em modelos específicos. API key/HTTP Digest varia. Não encontrei nem validei API genérica pública de terceiros para o Connect nesta pesquisa. | monitoramento, upload e start/cancel por família/dispositivo | Integração possível dentro do ecossistema Prusa, sem cobertura geral.
| Bambu Lab | 🔴 Manual oficial fala do fluxo de app/cloud por produto; não foi achada documentação pública oficial de API local genérica nesta pesquisa. MQTT/FTPS aparece em implementações comunitárias, então deve ser tratado como protocolo não oficial. | Não validado contra documentação API oficial | Risco alto para MVP; não integrar sem aparelho e confirmação atual por modelo/firmware.
| Creality Cloud / OS | 🔴 Manuais oficiais documentam app/serviços por família/modelo; não foi achada API pública genérica para serviço de terceiro nesta pesquisa. | Não validado contra documentação API oficial | Heterogêneo; trabalho futuro.
| Farm mock | ✅ Executado; JSON estático local | listar, escolher candidato, capacidade teórica | Simulação local; não hardware nem telemetria real.

## Recomendação

Escolher **OctoPrint** para a primeira integração real quando hardware do usuário estiver disponível: a API oficial documenta estados e job operations em HTTP. Para farms Klipperizadas, adicionar Moonraker em segundo lugar. Não prometer “todas as impressoras”: suporte depende de firmware, plugin/server e configuração. Até haver hardware, o MVP só faz orçamento e recomendação em estado mock/manual; iniciar, pausar e cancelar ficam fora do lançamento.

## Segurança

OctoPrint recomenda API key; Moonraker normalmente requer configuração de trusted clients/proxy. Não expor API direto à Internet. Usar VPN/LAN segura, segredo fora de logs/prompt, validação TLS e confirmação humana para iniciar/cancelar. O `curl -k` do skill existente desabilita TLS e não é recomendação de produção.

## Fontes oficiais/projeto

- [OctoPrint API overview](https://docs.octoprint.org/en/master/api/), [Printer API](https://docs.octoprint.org/en/master/api/printer.html), [Job API](https://docs.octoprint.org/en/master/api/job.html), [File API](https://docs.octoprint.org/en/master/api/files.html).
- [Moonraker API](https://moonraker.readthedocs.io/en/latest/web_api/), [authorization](https://moonraker.readthedocs.io/en/latest/web_api/#authorization).
- [Prusa Connect and PrusaLink explained](https://help.prusa3d.com/article/prusa-connect-and-prusalink-explained_302608), [PrusaLink control and security](https://help.prusa3d.com/article/prusalink-sl1-sl1s_146094).
- [Bambu Lab Wiki](https://wiki.bambulab.com/en/home) e [Creality Wiki](https://wiki.creality.com/); conferir instruções do modelo exato antes de qualquer integração.
