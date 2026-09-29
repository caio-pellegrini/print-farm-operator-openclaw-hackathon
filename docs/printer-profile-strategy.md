# Estratégia de perfis de impressora

## Conclusão para onboarding

Não inferir um perfil confiável só de “Ender 3 V3 SE + PLA”. Modelo/nozzle/material são insuficientes: firmware e revisão, limites de velocidade/aceleração, extrusor, retração e ajustes do usuário mudam segurança e previsão. MVP deve aceitar um preset explicitamente importado/exportado para Cura ou PrusaSlicer, validar máquina/volume/material/nozzle, e oferecer poucos perfis conhecidos como templates que o usuário confirma. Rotular qualquer estimativa com origem e versão do perfil.

## O que o checkout existente faz

`pla_profile.json` tem `inherits: fdmprinter` e overrides de layer height, line width, diâmetro, temperaturas, velocidades, infill, shell e adesão. O Dockerfile instala CuraEngine e copia 2.8 MB de definições do pacote Cura 5.0.0. Elas incluem `creality_ender3.def.json` antigo, mas não Ender 3 V3 SE. Não há start/end G-code V3 SE no pacote testado.

## Cura

Cura usa definições de máquina e extrusor mais arquivos de qualidade/settings. Profile JSON de qualidade não substitui a definição da impressora. Para “Ender 3 V3 SE”, deve-se fornecer a definition correta de máquina, extruder, material e qualidade, exportadas do Cura compatível; revisar `machine_start_gcode`, limites e offsets. O skill original corta usando defaults, mas ficou com diâmetro 2.85 mm. Definir `-e0 -s material_diameter=1.75` corrigiu o comprimento reportado de 0.477202 m para 1.26566 m, mantendo 3044 mm³. Um JSON de qualidade isolado não basta para estimar massa com confiança.

## PrusaSlicer

Presets podem ser exportados/importados como bundle e incluem impressora, filamento e print settings. Isso tende a ser mais claro para fluxo headless. Usuário importa preset aprovado ou escolhe modelo conhecido e confirma bico/material. Guardar bundle/versionamento para reprodutibilidade.

## OrcaSlicer

Presets integrados são atraentes para famílias populares, mas confirmar versão/CLI e origem do preset. Fluxo exportado/importado deve ser testado em Linux sem GUI antes de prometer integração.

## Campos mínimos do setup

1. Nome, slicer e versão.
2. Perfil oficial/importado da impressora, dimensões X/Y/Z e nozzle.
3. Material e diâmetro; filamento carregado em cada máquina.
4. Layer height, temperatura e preset de qualidade.
5. Suportes/infill/orientação aprovados pelo cliente ou operador.
6. Confirmação explícita antes de subir arquivo ou iniciar trabalho.

## Fontes

- [CuraEngine](https://github.com/Ultimaker/CuraEngine) e [Cura settings/definitions](https://github.com/Ultimaker/Cura/tree/main/resources).
- [PrusaSlicer presets: importar/exportar](https://help.prusa3d.com/article/exporting-and-importing-presets_2004).
- [PrusaSlicer CLI](https://help.prusa3d.com/article/command-line-interface_177484).
- [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer).
