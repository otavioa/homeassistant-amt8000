# Intelbras AMT 8000 v2.0 — Home Assistant Integration

[![HACS Custom][hacs-shield]][hacs-url]
[![GitHub Release][release-shield]][release-url]

Native Home Assistant integration v2.0 for the **Intelbras AMT 8000** alarm panel, communicating directly over the local network via the ISECNet v2 protocol. No cloud dependency at runtime.

## Features

- **Arm / Disarm per partition** — individual control of each configured partition
- **Zone monitoring** — binary sensor per zone (open / closed / violated)
- **Configurable zone types** — choose the Home Assistant device class for each zone; Door / Open-Closed is the default
- **Open-zone protection** — arming can bypass open zones and arm in the same Home Assistant action when the switch is enabled
- **Live siren detection** — binary sensor + event entity for automations
- **Device triggers** — "Alarm triggered" trigger in the automation UI (no YAML needed)
- **Local only** — direct TCP connection to the panel on port 9009

## Tested hardware

| Model | Firmware | Status |
|-------|----------|--------|
| AMT 8000 (model byte `0x8B`) | 3.2.5 | ✅ Working |

## Installation via HACS

1. Open HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/otavioa/homeassistant-amt8000` — Category: **Integration**
3. Install **Intelbras AMT 8000 v2.0** and restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration** → search **Intelbras AMT 8000**
5. Enter your panel's IP, port (default `9009`), and password

After setup, open the integration options to configure the device class of each zone. The default is **Door — Open/Closed**.

## Local diagnostic utility

The repository includes [`amt8000_tool.py`](amt8000_tool.py), a standalone utility that does not require Home Assistant to be installed. It reads the panel model, firmware, partitions, zones, siren, battery and tamper status, and can display the raw status frame in hexadecimal.

Read-only examples:

```bash
python3 amt8000_tool.py --host 192.168.1.100 status
python3 amt8000_tool.py --host 192.168.1.100 --json status
python3 amt8000_tool.py --host 192.168.1.100 raw-status
python3 amt8000_tool.py --host 192.168.1.100 --trace-auth status
python3 amt8000_tool.py --host 192.168.1.100 mac --execute
python3 amt8000_tool.py --host 192.168.1.100 keep-alive --execute
python3 amt8000_tool.py --host 192.168.1.100 arm --partition 1 --mode stay
python3 amt8000_tool.py --host 192.168.1.100 watch --interval 5
```

Para armar com zonas abertas, ative o bypass das zonas e depois repita o arme normal:

```bash
python3 amt8000_tool.py --host 192.168.1.100 bypass --zone 3 --execute
python3 amt8000_tool.py --host 192.168.1.100 arm --partition 1 --mode away --execute
```

`raw-status` detalha o frame completo, separando cabeçalho, comando, checksum e os blocos do payload: modelo, firmware, máscaras de zonas, estado global, partições, tamper e bateria.

Commands that change the panel state are disabled by default. Review the generated frame and use `--execute` only when the operation is intentional:

```bash
python3 amt8000_tool.py --host 192.168.1.100 arm --partition 1 --execute
python3 amt8000_tool.py --host 192.168.1.100 disarm --partition 1 --execute
python3 amt8000_tool.py --host 192.168.1.100 bypass --zone 3 --execute
python3 amt8000_tool.py --host 192.168.1.100 panic --type audible --execute
python3 amt8000_tool.py --host 192.168.1.100 siren-off --execute
python3 amt8000_tool.py --host 192.168.1.100 pgm --index 0 --state on --execute
```

Todas as operações exibem a requisição e a resposta detalhadas, incluindo payload, ACK/NACK e checksum. Use `--trace-auth` para ver a estrutura da autenticação com os seis bytes da senha mascarados. Pânico, sirene e PGM podem causar efeitos físicos e exigem revisão cuidadosa antes de usar `--execute`. A senha é solicitada interativamente e não é exibida. Não compartilhe capturas de autenticação ou relatórios contendo detalhes da rede.

## Entities created

| Entity | Type | Description |
|--------|------|-------------|
| `alarm_control_panel.amt8000_partition_N` | Alarm panel | One per configured partition. Arm Away / Disarm. |
| `alarm_control_panel.amt8000_all_partitions` | Alarm panel | Virtual master panel that arms or disarms all user partitions at once. |
| `binary_sensor.amt8000_zone_N` | Binary sensor | Open / closed. Extra attrs: violated, bypassed, tamper, low_battery. |
| `binary_sensor.amt8000_siren` | Binary sensor (sound) | True while siren is actively sounding. |
| `switch.amt8000_allow_open_zone_bypass` | Switch | Allows automatic bypass of open zones when arming. Off by default. |
| `event.amt8000_alarm` | Event | Fires `alarm_triggered` on siren rising edge. |

## Protocol notes

The AMT 8000 uses **ISECNet v2** (TCP 9009), which is distinct from the `0xe7` protocol used by lower-end AMT models (1016/2018 NET).

Partition index 0 in the protocol is a read-only AND-aggregate (armed only when all real partitions are armed) and is intentionally excluded from HA entities. User partitions start at protocol index 1. The virtual **All Partitions** entity uses the protocol command `0xFF` to arm or disarm all user partitions.

Full protocol documentation in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## License

MIT

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://hacs.xyz
[release-shield]: https://img.shields.io/github/v/release/otavioa/homeassistant-amt8000.svg
[release-url]: https://github.com/otavioa/homeassistant-amt8000/releases
