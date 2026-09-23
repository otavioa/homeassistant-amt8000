# Protocolo ISECNet — Intelbras AMT 8000

Este documento reúne o conhecimento disponível sobre o **ISECNet V2** usado pela AMT 8000, com foco na comunicação TCP direta local da integração.

O protocolo é proprietário. As informações combinam observação de tráfego da AMT 8000, esta integração, o SDK Intelbras V2 (`docs/SDKCentraisDeAlarmeIntelbras-v1.0.1/SDKCentraisDeAlarmeIntelbras-v1.0.1.xlsx` e o mapa de comandos `STATUS_COMPLETO` / `0x0B4A`) e a engenharia reversa do projeto [`guardian-api-intelbras`](https://github.com/bobaoapae/guardian-api-intelbras). Os itens marcados como não validados ainda precisam ser confirmados na central local.

No SDK, o campo Data é **1-based**. Neste documento e no código, `payload[n]` = Byte SDK `n+1`.

## Escopo e variantes

O projeto atual usa conexão direta com a central na porta TCP `9009`:

```text
cliente → central AMT 8000:9009
```

O projeto de referência também implementa fluxos Cloud/Relay e ISECNet V1. Esses fluxos não devem ser misturados com o transporte local V2 sem validação.

| Variante | Uso | Situação neste projeto |
|----------|-----|-----------------------|
| ISECNet V2 local | AMT 8000 via TCP/9009 | Em uso |
| ISECNet V2 Cloud/Relay | Aplicativo/servidor Intelbras | Referência externa |
| ISECNet V1 | Modelos legados e IP Receiver | Fora do escopo |

## Estrutura do frame V2

```text
[destination:2][source:2][size:2][command:2][payload:N][checksum:1]
```

| Campo | Tamanho | Descrição |
|-------|---------|-----------|
| `destination` | 2 bytes | Destino. Na requisição local observada: `00 00`. |
| `source` | 2 bytes | Origem. Na requisição local observada: `8F E0`. |
| `size` | 2 bytes | Big-endian; tamanho de `command + payload`, sem checksum. |
| `command` | 2 bytes | Código do comando em big-endian. |
| `payload` | N bytes | Dados específicos do comando. |
| `checksum` | 1 byte | XOR de todos os bytes anteriores, depois XOR com `0xFF`. |

Na resposta, destino e origem podem aparecer invertidos. Isso foi observado no comando `GET_MAC`:

```text
requisição: 00 00 8F E0 ...
resposta:   8F E0 00 00 ...
```

### Checksum

```python
def checksum(data: list[int]) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return (result ^ 0xFF) & 0xFF
```

O campo `size` inclui os dois bytes do comando. Para uma requisição sem payload, `size = 2`.

## Comandos V2 conhecidos

O catálogo abaixo vem da documentação/implementação do `guardian-api-intelbras`. “Validado localmente” significa que a central desta integração respondeu corretamente.

| Comando | Código | Payload conhecido | Validação local |
|---------|--------|-------------------|-----------------|
| `AUTHORIZE` | `0xF0F0` | Variante local: `[device_type, p1..p6, sw_version]` | Sim |
| `KEEP_ALIVE` | `0xF0F7` | Sem payload | Adicionado ao utilitário; pendente resposta real |
| `DISCONNECT` | `0xF0F1` | Sem payload | Sim, usado ao fechar sessão |
| `SYSTEM_ARM_DISARM` | `0x401E` | `[partition, operation]` | Sim, arme/desarme |
| `ALARM_PANEL_STATUS` | `0x0B4A` | Sem payload na requisição | Sim |
| `DISPOSITIVOS_CADASTRADOS` | `0x0B50` | Sem payload na requisição; resposta: 29 bytes | Sim: tool `devices` e `get_status` do cliente (PGMs e sirenes RF cadastradas) |
| `PANIC_ALARM` | `0x401A` | `[panic_type]` | Sim: tool `panic` e entidade HA `siren` (`turn_on` + tone) |
| `TURN_OFF_SIREN` | `0x4019` | Sem payload | Sim: tool `siren-off` e entidade HA `siren` (`turn_off`) |
| `BYPASS_ZONE` | `0x401F` | `[zone_index, bypass]` | Sim: anular (`0x01`) e reativar (`0x00`) por zona. `0x01` também com a central armada (Guardian e `amt8000_tool.py`); `0x00` via `amt8000_tool.py --clear` |
| `GET_MAC` | `0x3FAA` | `[0x00]` | Sim |
| `PGM_ON_OFF` | `0x45AF` | `[pgm_index, state]` | Sim: índice `0` ligar/desligar com ACK local. Índices `1`–`15` aceitos pelo cliente; `8`–`15` ainda sem captura |

O projeto de referência também lista `CONNECT (0x30F6)` e `APP_CONNECT (0xFFF1)` para fluxos Cloud/Relay. Eles não fazem parte do fluxo local simplificado atualmente utilizado por este projeto.

## Respostas V2

| Resposta | Código | Significado |
|----------|--------|-------------|
| `ACK` | `0xF0FE` | Comando aceito |
| `NACK` | `0xF0FD` | Comando rejeitado; normalmente `payload[0]` contém o erro |

Para o status, a central responde com o próprio comando `0x0B4A` e o payload de status. O projeto de referência considera respostas diferentes de `NACK` como sucesso, mas isso deve ser tratado com cautela em comandos de controle.

### Respostas de autenticação

| Código | Significado |
|--------|-------------|
| `0x00` | Aceita |
| `0x01` | Senha inválida |
| `0x02` | Usuário bloqueado |
| `0x03` | Sem permissão |

### Erros de bypass

Códigos de NACK (`0xF0FD`) documentados na referência ISECNet. O primeiro byte do payload é o código.

| Código | Significado (referência) | Observado na AMT 8000 |
|--------|--------------------------|------------------------|
| `0xE6` | Bypass negado | Possível |
| `0xE8` | Documentado como “bypass com central armada” | **Não observado.** `0x401F` com a central armada retornou ACK no Guardian e no `amt8000_tool.py`. |
| `0x37` (`55`) | Sem permissão | Possível |

Não tratar `0xE8` como bloqueio de produto: anular zona com a central armada é válido no transporte local desta AMT.

## Fluxo local implementado

Cada operação abre uma conexão, autentica, envia um comando, lê uma resposta quando aplicável e fecha a sessão:

```text
1. TCP connect em <central>:9009
2. AUTHORIZE (0xF0F0)
3. comando da operação
4. resposta da central
5. DISCONNECT (0xF0F1)
```

### Autenticação local observada

O cliente atual envia:

```text
[0x00][password_6_bytes][0x10]
```

A senha usa a codificação Contact-ID usada pelo projeto:

- um byte por dígito;
- o dígito `0` é codificado como `0x0A`;
- senhas de quatro dígitos recebem dois `0x0A` à esquerda;
- o último byte atual é `0x10`.

Exemplo estrutural para uma senha de quatro dígitos:

```text
[00][0A][0A][d1][d2][d3][d4][10]
```

O projeto de referência descreve outra variante de autenticação para determinados fluxos, com `[0x03][password_6_bytes][0x01][0x00]`. Essa diferença deve ser preservada até que o transporte local seja comparado com o fluxo Cloud/Relay.

## Status `0x0B4A`

A requisição não possui payload relevante. Na central observada, a resposta contém **143 bytes de payload**.

```text
payload = frame[8 : 8 + (size - 2)]
```

Tamanho fixo no SDK: mínimo = máximo = **143** bytes (`size` do frame `0x0091`).

Máscaras de zona no SDK têm **64 bits** (8 bytes). O cliente HA ainda lê **56 bits** (7 bytes), no recorte do projeto de referência; o 8º byte de cada máscara é zonas 57–64.

### Payload de status

| Offset | Byte SDK | Campo | Notas |
|--------|----------|-------|-------|
| `0` | 1 | Modelo | Observado `0x8B` no firmware `3.2.5`. O SDK cita `0x01` para AMT 8000. |
| `1–3` | 2–4 | Firmware | `major.minor.patch`. Validado. |
| `4–5` | 5–6 | Recursos | Partições, WiFi, Ethernet, GPRS, PSTN, câmera. SDK; HA não usa. |
| `6` | 7 | Índice do usuário autenticado | SDK; HA não usa. |
| `7` | 8 | Permissões | Stay / desarmar / bypass. SDK; HA não usa. |
| `8–9` | 9–10 | Partições do usuário | 16 bits: quais o usuário autenticado pode usar. SDK; HA não usa. |
| `10–11` | 11–12 | Stay por partição | SDK; HA não usa. |
| `12–19` | 13–20 | Zonas do usuário | 64 bits. HA trata `12–18` (56 zonas) como habilitadas. |
| `20` | 21 | Estado global | Validado; HA usa. |
| `21–37` | 22–38 | Partições 0–16 | 17 bytes no SDK. HA lê `21–36` (16). |
| `38–45` | 39–46 | Zonas abertas | 64 bits. HA lê `38–44` (56). |
| `46–53` | 47–54 | Zonas em alarme / violadas | 64 bits. HA lê `46–52` (56). |
| `54–61` | 55–62 | Bypass | 64 bits. HA lê 56 bits a partir de `54`. |
| `62–63` | 63–64 | Sirenes 1 e 2 | SDK; HA ainda não usa como status ao vivo por unidade. |
| `64–69` | 65–70 | Relógio BCD | Dia, mês, ano, hora, minuto, segundo. Validado na captura. |
| `70` | 71 | Pânico | SDK. |
| `71–72` | 72–73 | Falhas gerais | AC, bateria, RF, Ethernet, etc. HA usa `payload[71] bit 1` como tamper da central. |
| `73–80` | 74–81 | Falha de comunicação (sensor) | SDK. |
| `81–82` | 82–83 | Falha teclado | SDK. |
| `83–84` | 84–85 | Falha sirene | SDK; HA lê 16 bits para sirenes RF cadastradas (`fault`). Pendente captura com falha real. |
| `85–86` | 86–87 | Falha repetidor | SDK. |
| `87–88` | 88–89 | Falha comunicação PGM | 16 bits. HA lê para PGM cadastrada (`comm_fail`). SDK; sem captura de falha real. |
| `89–96` | 90–97 | Tamper sensor | 64 bits. HA lê `89–95` (56). |
| `97–102` | 98–103 | Tamper teclado / sirene / repetidor | SDK. Hipótese HA: `97–98` teclado, `99–100` sirene RF (`tamper`), `101–102` repetidor. Pendente captura. |
| `103–104` | 104–105 | Tamper PGM | 16 bits. HA lê para PGM cadastrada (`tamper`). SDK; sem captura de falha real. |
| `105–112` | 106–113 | Bateria baixa (sensor) | 64 bits. HA lê `105–111` (56). |
| `113–118` | 114–119 | Bateria teclado / sirene / repetidor | SDK. Hipótese HA: `113–114` teclado, `115–116` sirene RF (`low_battery`), `117–118` repetidor. Pendente captura. |
| `119–120` | 120–121 | Bateria baixa PGM | 16 bits. HA lê para PGM cadastrada (`low_battery`). SDK; sem captura de falha real. |
| `121–133` | 122–134 | Bateria keyfob | SDK. |
| `134` | 135 | Bateria da central | `1` morta, `2` baixa, `3` média, `4` cheia. Validado. |
| `135–136` | 136–137 | Sync RF | Tipo/índice do dispositivo no botão de sync (`0x06` = PGM). SDK. |
| `137–138` | 138–139 | PGM ligada/desligada | 16 bits. Bit 0 = OFF, bit 1 = ON. `[137]` = índices 0–7, `[138]` = 8–15. Validado na PGM 0. HA usa. Não diz se a PGM existe. |
| `139–142` | 140–143 | Fechadura | Estado, porta, falha, bateria. SDK. |

### Estado global — `payload[20]`

| Bits | Significado |
|------|-------------|
| `6:5` | `0` desarmada, `1` parcial, `3` armada total |
| `3` | Zonas disparando |
| `2` | Zonas fechadas |
| `1` | Sirene disparada (`siren_live`) — estado da entidade HA `siren` |

O evento HA `alarm_triggered` (entity `event` + bus `amt8000_alarm_triggered`) dispara na **borda de subida de `partition.firing`**, não em `siren_live`. Assim o evento permanece ligado ao alarme mesmo se a sirene for silenciada ou o pânico for silencioso.

### Status de cada partição

Cada byte em `payload[21:38]` (SDK: partições 0–16) usa:

| Bit | Significado |
|-----|-------------|
| `7` | Partição habilitada |
| `6` | Stay/parcial |
| `3` | Disparada |
| `2` | Disparando |
| `0` | Armada |

O índice `0` é um agregado somente leitura: seu bit de arme representa o `AND` das partições reais. As partições configuráveis começam no índice `1`.

## Dispositivos cadastrados — `0x0B50`

`DISPOSITIVOS_CADASTRADOS` lista o que está gravado na central (RF). Não substitui o status: não diz se a zona está aberta nem se a PGM está ligada.

Pedido sem payload. A central responde com o próprio `0x0B50` e **29 bytes** de Data. Bit **0** = não cadastrado, bit **1** = cadastrado. LSB de cada byte é o menor índice.

Layout (índices 0-based = Byte do SDK − 1):

| Bytes | Dispositivo | Numeração |
|-------|-------------|-----------|
| `0:13` | Controles / keyfobs | 0–97, LSB primeiro |
| `13:21` | Sensores (zonas) | 1–64 |
| `21:23` | Teclados | 1–16 |
| `23:25` | Sirenes | 1–16 |
| `25` bits 0–3 | Repetidores | 1–4 |
| `25` bits 4–7 | PGM 1–4 | bit4 = PGM 1 |
| `26` | PGM 5–12 | bit0 = PGM 5 |
| `27` bits 0–3 | PGM 13–16 | bit0 = PGM 13 |
| `28` | Reservado | — |

PGM no bitmap é 1-based. O cliente converte para índice 0-based do `0x45AF` (PGM 1 → `0`).

Captura local (AMT 8000, firmware 3.2.5):

```text
requisição: 00 00 8F E0 00 02 0B 50 C9
resposta:   8F E0 00 00 00 1F 0B 50 02 00 00 00 00 00 00 00 00 00 00 00 00 FF 00 00 00 00 00 00 00 00 00 01 00 10 00 00 00 38
payload:    02 00 00 00 00 00 00 00 00 00 00 00 00 FF 00 00 00 00 00 00 00 00 00 01 00 10 00 00 00
```

Decodificado: keyfob 1, zonas 1–8, sirene 1, PGM 1. Sem teclado nem repetidor. `payload[25] = 0x10` (bit4) = só PGM 1 cadastrada.

A tool consulta com `devices`. O cliente HA envia `0x0B50` na mesma sessão TCP do `0x0B4A` (`get_status`). Com NACK ou payload curto, a lista de PGM fica vazia — não inventa PGM 1 e 2. Switch no HA só para PGM com bit cadastrado.

## Sinal RF por zona — não disponível nesta AMT

O SDK V2 tem `NIVEL_DE_SINAL_DISP_SF` (`0x0B73`, 64 bytes, 1 por zona, 0–10). No firmware 3.2.8 a central responde 1 byte `0xEA` (checksum do pedido) — comando não implementado. `STATUS_GERAL_RF` (`0x0B40`) responde 3 bytes (`02 00 00` na captura local); é resumo de flags, não RSSI por zona. O status `0x0B4A` também não traz nível de sinal. O Guardian lê 0–10 só no ISECNet V1 `0x5D` (AMT 2018 E Smart / 1000 Smart) — não enviar na porta 9009 desta AMT 8000. Esta integração não expõe sinal RF.

## Arme e desarme — `0x401E`

Payload:

```text
[partition_index][operation]
```

| Byte | Valor | Significado |
|------|-------|-------------|
| `partition_index` | `0x01`–`0x0F` | Partição individual |
| `partition_index` | `0xFF` | Todas as partições |
| `operation` | `0x00` | Desarmar |
| `operation` | `0x01` | Armar total/away |
| `operation` | `0x02` | Armar stay/parcial — documentado no projeto irmão, não validado localmente |
| `operation` | `0x03` | Não utilizado; arme com zonas abertas usa bypass por zona e depois `0x01` |

O cliente atual usa `0x00` e `0x01`.

## Bypass — `0x401F`

Para ISECNet V2, o projeto de referência documenta uma operação por zona:

```text
[zone_index_zero_based][bypass]
```

| Byte | Valor | Significado |
|------|-------|-------------|
| `zone_index` | `0x00`–`0x37` | Zonas 1–56, convertidas para índice zero-based |
| `bypass` | `0x01` | Ativar bypass (anular). Validado localmente, inclusive com a central armada. |
| `bypass` | `0x00` | Remover bypass (reativar). Validado localmente com `amt8000_tool.py --clear` (ACK). Cliente HA e switch por zona usam o mesmo payload. |

O cliente envia uma requisição separada para cada zona. A confirmação deve ser `ACK (0xF0FE)`; em caso de `NACK (0xF0FD)`, o primeiro byte do payload é o código de erro.

No Home Assistant, cada zona habilitada tem um `switch` (`Zone N Bypass`): ligado anula (`0x01`), desligado reativa (`0x00`). O estado segue a máscara de zonas em bypass do status.

Para armar com zonas abertas, o fluxo validado é ativar o bypass de cada zona com `0x401F` e, em seguida, enviar `SYSTEM_ARM_DISARM` com `operation=0x01` (arme total/away). No Home Assistant, as duas operações são executadas pela mesma ação de arme quando `Allow Open Zone Bypass` está ligado; com o switch desligado, o arme é bloqueado e nenhuma zona é anulada. O utilitário não expõe um modo de arme forçado separado.

## GET MAC — `0x3FAA`

Payload da requisição:

```text
[0x00]
```

Na resposta observada, o MAC aparece depois de um byte inicial de resposta:

```text
response[9:-1] → seis bytes do MAC
```

O utilitário local já decodifica o resultado como `AA:BB:CC:DD:EE:FF`.

## KEEP-ALIVE — `0xF0F7`

Comando sem payload, usado para manter uma sessão persistente e verificar se a central continua respondendo. A integração atual abre e fecha conexões por operação, portanto ainda não depende dele.

## Pânico — `0x401A`

Payload de um byte:

| Valor | Tipo | Tone HA / tool `--type` |
|-------|------|-------------------------|
| `0` | Silencioso | `silent` |
| `1` | Audível | `audible` |
| `2` | Fogo | `fire` |
| `3` | Médico | `medical` |

Habilitado na tool (`panic --type … --execute`) e na entidade HA `siren` via `siren.turn_on` com `tone`. Efeito físico/operacional — exige confirmação explícita na tool.

## Desligar sirene — `0x4019`

Comando sem payload para silenciar a sirene mantendo o estado de arme. Habilitado na tool (`siren-off --execute`) e na entidade HA `siren` via `siren.turn_off`.

## Sirenes RF no status e no HA

Cadastro: `0x0B50` bytes `23:25` (sirenes 1–16). O cliente monta uma lista `sirens` no `PanelStatus` só para números cadastrados.

Diagnóstico (attrs do `binary_sensor` por sirene RF):

| Campo | Offset (0-based) | Nota |
|-------|------------------|------|
| `fault` | `83–84` | Faixa dedicada no SDK. Pendente captura com falha real. |
| `tamper` | `99–100` | Hipótese (após 2 bytes de teclado em `97–98`). Pendente captura. |
| `low_battery` | `115–116` | Hipótese (após 2 bytes de teclado em `113–114`). Pendente captura. |

Validar com a tool antes de confiar nos bits de trouble:

```bash
python3 amt8000_tool.py --host <IP> devices
python3 amt8000_tool.py --host <IP> status
python3 amt8000_tool.py --host <IP> raw-status
```

Não há controle individual por sirene RF neste projeto.

## PGM — `0x45AF`

Payload documentado:

```text
[pgm_index][state]
```

| Campo | Valores |
|-------|---------|
| `pgm_index` | `0x00`–`0x0F` (PGMs 1–16) |
| `state` | `0x00` desligado, `0x01` ligado |

O cliente e o `amt8000_tool.py` enviam esse frame. Índice `0` foi validado localmente com ACK ao ligar e ao desligar. Índices `1`–`15` são aceitos pelo cliente; `8`–`15` ainda não têm captura nesta central.

### PGM: on/off no status, cadastro no `0x0B50`

Estado ligado/desligado: `payload[137:139]`, 16 bits, mesma ordem das zonas (LSB = menor índice do byte).

| Byte | Índices |
|------|---------|
| `137` | 0–7 |
| `138` | 8–15 |

Confirmado: PGM 0 ligada → `payload[137] = 0x01`; desligada → `0x00`. Os bytes `19` e `37` não são on/off de PGM: no SDK são o 8º byte da máscara de zonas do usuário e o status da partição 16.

Quais PGMs existem **não está no `0x0B4A`**. O cadastro (bitmap RF, inclusive PGM) está em [Dispositivos cadastrados — `0x0B50`](#dispositivos-cadastrados--0x0b50).

O switch no HA expõe `index`, `number`, `tamper` (`payload[103:105]`), `low_battery` (`payload[119:121]`) e `comm_fail` (`payload[87:89]`). Ordem LSB igual ao on/off. Bits de falha vêm do SDK; ainda sem captura com PGM em defeito.

## Códigos de modelo

O código do modelo não deve ser generalizado sem observar a mesma variante de transporte e firmware.

Para a central usada nesta integração, o valor observado foi:

```text
payload[0] = 0x8B
firmware = 3.2.5
```

O projeto de referência possui uma tabela de códigos de modelo diferente, associada ao fluxo Cloud/Relay. Essa tabela não substitui a observação da resposta local da AMT 8000.

## Referências

- SDK Intelbras V2 — `docs/SDKCentraisDeAlarmeIntelbras-v1.0.1/SDKCentraisDeAlarmeIntelbras-v1.0.1.xlsx` (envelope ISECNet V2; mapa `0x0B4A` / `0x0B50`)
- [`bobaoapae/guardian-api-intelbras`](https://github.com/bobaoapae/guardian-api-intelbras) — projeto de referência original
- [`caarlos0/homekit-amt8000`](https://github.com/caarlos0/homekit-amt8000) — implementação Go referenciada pelo cliente original
- [`merencia/amt8000-hass-integration`](https://github.com/merencia/amt8000-hass-integration) — cliente Python referenciado pelo projeto original
- [`elvis-epx/alarme-intelbras`](https://github.com/elvis-epx/alarme-intelbras) — receptor IP referenciado pelo projeto original

As informações de engenharia reversa do projeto de referência indicam análise do aplicativo oficial Guardian Android. Isso não equivale a uma especificação oficial pública da Intelbras.
