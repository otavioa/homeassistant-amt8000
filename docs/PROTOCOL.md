# Protocolo ISECNet — Intelbras AMT 8000

Este documento reúne o conhecimento disponível sobre o **ISECNet V2** usado pela AMT 8000, com foco na comunicação TCP direta local da integração.

O protocolo é proprietário. As informações combinam observação de tráfego da AMT 8000, implementação desta integração e a documentação/engenharia reversa do projeto [`guardian-api-intelbras`](https://github.com/bobaoapae/guardian-api-intelbras). Os itens marcados como não validados ainda precisam ser confirmados na central local.

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
| `PANIC_ALARM` | `0x401A` | `[panic_type]` | Não; não habilitado |
| `TURN_OFF_SIREN` | `0x4019` | Sem payload | Não; não habilitado |
| `BYPASS_ZONE` | `0x401F` | `[zone_index, bypass]` | Sim, ativação de bypass por zona |
| `GET_MAC` | `0x3FAA` | `[0x00]` | Sim |
| `PGM_ON_OFF` | `0x45AF` | `[pgm_index, state]` | Não; não habilitado |

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

| Código | Significado |
|--------|-------------|
| `0xE6` | Bypass negado |
| `0xE8` | Bypass com central armada |
| `0x37` (`55`) | Sem permissão |

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

### Payload de status

| Offset | Campo | Descrição |
|--------|-------|-----------|
| `0` | Modelo | Na AMT 8000 observada com firmware `3.2.5`: `0x8B`. |
| `1–3` | Firmware | `major.minor.patch`. |
| `4–11` | Desconhecido | Não mapeado. |
| `12–18` | Zonas habilitadas | Máscara de 56 zonas. LSB é a zona inicial de cada byte. |
| `19` | Desconhecido | Não mapeado. |
| `20` | Estado global | Estado de arme, zonas, sirene. |
| `21–36` | Partições | 16 bytes, um por índice de partição. |
| `37` | Desconhecido | Não mapeado. |
| `38–44` | Zonas abertas | Máscara de 56 zonas. |
| `45` | Desconhecido | Não mapeado. |
| `46–52` | Zonas violadas | Máscara de 56 zonas. |
| `53` | Desconhecido | Não mapeado. |
| `54–61` | Zonas em bypass | Máscara de 56 zonas; o oitavo byte ainda precisa ser confirmado. |
| `62–70` | Desconhecido | Não mapeado. |
| `71` | Tamper da central | Bit `1` indica tamper. |
| `72–88` | Desconhecido | Não mapeado. |
| `89–95` | Tamper de zonas | Máscara de 56 zonas. |
| `96–104` | Desconhecido | Não mapeado. |
| `105–111` | Bateria baixa | Máscara de 56 zonas. |
| `112–133` | Desconhecido | Não mapeado. |
| `134` | Bateria da central | `1` morta, `2` baixa, `3` média, `4` cheia. |
| `135–142` | Desconhecido | Não mapeado. |

### Estado global — `payload[20]`

| Bits | Significado |
|------|-------------|
| `6:5` | `0` desarmada, `1` parcial, `3` armada total |
| `3` | Zonas disparando |
| `2` | Zonas fechadas |
| `1` | Sirene ativa |

### Status de cada partição

Cada byte em `payload[21:37]` usa:

| Bit | Significado |
|-----|-------------|
| `7` | Partição habilitada |
| `6` | Stay/parcial |
| `3` | Disparada |
| `2` | Disparando |
| `0` | Armada |

O índice `0` é um agregado somente leitura: seu bit de arme representa o `AND` das partições reais. As partições configuráveis começam no índice `1`.

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
| `bypass` | `0x01` | Ativar bypass |
| `bypass` | `0x00` | Remover bypass; ainda não exposto pela integração |

O cliente envia uma requisição separada para cada zona. A confirmação deve ser `ACK (0xF0FE)`; em caso de `NACK (0xF0FD)`, o primeiro byte do payload é o código de erro.

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

O projeto de referência documenta payload de um byte:

| Valor | Tipo |
|-------|------|
| `0` | Silencioso |
| `1` | Audível |
| `2` | Fogo |
| `3` | Médico |

Não está habilitado neste projeto por ter efeito físico/operacional direto.

## Desligar sirene — `0x4019`

O projeto de referência indica comando sem payload para silenciar a sirene mantendo o estado de arme. Não está habilitado neste projeto.

## PGM — `0x45AF`

Payload documentado:

```text
[pgm_index][state]
```

| Campo | Valores |
|-------|---------|
| `pgm_index` | `0x00`–`0x07` |
| `state` | `0x00` desligado, `0x01` ligado |

Não está habilitado neste projeto.

## Códigos de modelo

O código do modelo não deve ser generalizado sem observar a mesma variante de transporte e firmware.

Para a central usada nesta integração, o valor observado foi:

```text
payload[0] = 0x8B
firmware = 3.2.5
```

O projeto de referência possui uma tabela de códigos de modelo diferente, associada ao fluxo Cloud/Relay. Essa tabela não substitui a observação da resposta local da AMT 8000.

## Referências

- [`bobaoapae/guardian-api-intelbras`](https://github.com/bobaoapae/guardian-api-intelbras) — projeto de referência original
- [`caarlos0/homekit-amt8000`](https://github.com/caarlos0/homekit-amt8000) — implementação Go referenciada pelo cliente original
- [`merencia/amt8000-hass-integration`](https://github.com/merencia/amt8000-hass-integration) — cliente Python referenciado pelo projeto original
- [`elvis-epx/alarme-intelbras`](https://github.com/elvis-epx/alarme-intelbras) — receptor IP referenciado pelo projeto original

As informações de engenharia reversa do projeto de referência indicam análise do aplicativo oficial Guardian Android. Isso não equivale a uma especificação oficial pública da Intelbras.
