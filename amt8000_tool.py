#!/usr/bin/env python3
"""Utilitário local de diagnóstico da central Intelbras AMT 8000."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path


def load_client_module():
    """Load the protocol client without importing Home Assistant."""
    client_path = Path(__file__).parent / "custom_components" / "amt8000" / "client.py"
    spec = importlib.util.spec_from_file_location("amt8000_client", client_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Não foi possível carregar {client_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_client = load_client_module()
ALL_PARTITIONS = _client.ALL_PARTITIONS
Amt8000Client = _client.Amt8000Client
PanelStatus = _client.PanelStatus
CannotConnect = _client.CannotConnect
InvalidAuth = _client.InvalidAuth

STATUS_COMMAND = bytes([0x0B, 0x4A])
ARM_COMMAND = bytes([0x40, 0x1E])
BYPASS_COMMAND = bytes([0x40, 0x1F])
KEEP_ALIVE_COMMAND = bytes([0xF0, 0xF7])
GET_MAC_COMMAND = bytes([0x3F, 0xAA])
PANIC_COMMAND = bytes([0x40, 0x1A])
SIREN_OFF_COMMAND = bytes([0x40, 0x19])
PGM_COMMAND = bytes([0x45, 0xAF])


class DiagnosticClient(Amt8000Client):
    """Expose the raw status frame without duplicating protocol encoding."""

    def __init__(self, host: str, port: int, password: str, trace_auth: bool = False) -> None:
        super().__init__(host, port, password)
        self.trace_auth = trace_auth

    async def _connect_and_auth(self):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise CannotConnect(f"Cannot connect to {self._host}:{self._port}") from exc

        auth_payload = [0x00] + self._encode_password(self._password) + [0x10]
        auth_frame = self._packet([0xF0, 0xF0], auth_payload)
        if self.trace_auth:
            print_auth_trace(auth_frame, auth_payload)
        writer.write(auth_frame)
        await writer.drain()

        response = await self._read_frame(reader)
        if self.trace_auth:
            print_command_response(response)
        result = response[8] if len(response) > 8 else -1
        if result == 0x01:
            writer.close()
            raise InvalidAuth("Invalid password")
        if result != 0x00:
            writer.close()
            raise CannotConnect(f"Auth rejected: code=0x{result:02X}")

        return reader, writer

    async def get_status_frame(self) -> tuple[PanelStatus, bytes, bytes]:
        reader, writer = await self._connect_and_auth()
        request = self._packet(list(STATUS_COMMAND))
        try:
            writer.write(request)
            await writer.drain()
            frame = await self._read_frame(reader)
        finally:
            await self._disconnect(writer)

        payload_length = int.from_bytes(frame[4:6], "big") - 2
        payload = frame[8 : 8 + payload_length]
        return self._parse_status(payload), frame, request

    async def send_command_frame(self, command: bytes, payload: bytes = b"") -> bytes:
        reader, writer = await self._connect_and_auth()
        try:
            writer.write(self._packet(list(command), list(payload)))
            await writer.drain()
            return await self._read_frame(reader)
        finally:
            await self._disconnect(writer)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnóstico local da Intelbras AMT 8000 via ISECNet v2."
    )
    parser.add_argument("--host", help="IP ou hostname da central")
    parser.add_argument("--port", type=int, default=9009, help="Porta TCP (padrão: 9009)")
    parser.add_argument("--json", action="store_true", help="Exibe a saída em JSON quando aplicável")
    parser.add_argument(
        "--trace-auth",
        action="store_true",
        help="Exibe a estrutura da autenticação com a senha mascarada",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Consulta e decodifica o status")
    subparsers.add_parser("raw-status", help="Exibe o frame de status em hexadecimal")

    for command, description in (
        ("mac", "Consulta o endereço MAC da central"),
        ("keep-alive", "Testa o comando keep-alive"),
    ):
        probe = subparsers.add_parser(command, help=description)
        probe.add_argument(
            "--execute",
            action="store_true",
            help="Confirma que o comando deve ser enviado à central",
        )

    panic = subparsers.add_parser("panic", help="Dispara um tipo de pânico")
    panic.add_argument(
        "--type",
        choices=("silent", "audible", "fire", "medical"),
        default="audible",
        help="Tipo de pânico (padrão: audible)",
    )
    panic.add_argument("--execute", action="store_true", help="Confirma o envio à central")

    siren = subparsers.add_parser("siren-off", help="Desliga a sirene ativa")
    siren.add_argument("--execute", action="store_true", help="Confirma o envio à central")

    pgm = subparsers.add_parser("pgm", help="Liga ou desliga uma saída PGM")
    pgm.add_argument("--index", type=int, required=True, help="Índice PGM de 0 a 7")
    pgm.add_argument("--state", choices=("on", "off"), required=True)
    pgm.add_argument("--execute", action="store_true", help="Confirma o envio à central")

    watch = subparsers.add_parser("watch", help="Monitora o status continuamente")
    watch.add_argument("--interval", type=float, default=5.0, help="Intervalo em segundos")
    watch.add_argument("--count", type=int, default=0, help="Quantidade de leituras; 0 = infinito")

    for command, action in (("arm", "arme"), ("disarm", "desarme")):
        control = subparsers.add_parser(command, help=f"Executa {action} de uma partição")
        control.add_argument(
            "--partition",
            required=True,
            help="Número da partição (1-15) ou 'all' para todas",
        )
        if command == "arm":
            control.add_argument(
                "--mode",
                choices=("away", "stay"),
                default="away",
                help="Modo de arme (padrão: away)",
            )
        control.add_argument(
            "--execute",
            action="store_true",
            help="Confirma que o comando deve ser enviado à central",
        )

    bypass = subparsers.add_parser("bypass", help="Faz bypass de uma ou mais zonas")
    bypass.add_argument("--zone", type=int, action="append", required=True, help="Zona de 1 a 56; repita a opção")
    bypass.add_argument(
        "--clear",
        action="store_true",
        help="Remove o bypass (reativa a zona) em vez de anular",
    )
    bypass.add_argument(
        "--execute",
        action="store_true",
        help="Confirma que o comando deve ser enviado à central",
    )

    return parser


def require_host(args: argparse.Namespace) -> str:
    if not args.host:
        raise SystemExit("Informe --host para conectar à central.")
    return args.host


def read_password() -> str:
    return getpass.getpass("Senha da central: ")


def frame_is_valid(frame: bytes) -> bool:
    if len(frame) < 9:
        return False
    declared_length = int.from_bytes(frame[4:6], "big")
    expected_length = 6 + declared_length + 1
    if len(frame) != expected_length:
        return False
    return frame[-1] == Amt8000Client._checksum(list(frame[:-1]))


def hex_bytes(value: bytes) -> str:
    return value.hex(" ").upper()


def enabled_indexes(mask: bytes, limit: int, start: int = 1) -> list[int]:
    indexes = []
    for index in range(limit):
        byte_index, bit_index = divmod(index, 8)
        if byte_index < len(mask) and mask[byte_index] & (1 << bit_index):
            indexes.append(start + index)
    return indexes


def print_mask_block(label: str, payload: bytes, start: int, end: int, limit: int) -> None:
    block = payload[start : end + 1]
    indexes = enabled_indexes(block, limit)
    decoded = ", ".join(map(str, indexes)) if indexes else "nenhum"
    print(f"  payload[{start}:{end}] {label}: {hex_bytes(block)} -> {decoded}")


def print_status_frame_details(frame: bytes) -> None:
    """Print the frame layout and all known status payload blocks."""
    payload_length = int.from_bytes(frame[4:6], "big") - 2
    payload = frame[8 : 8 + payload_length]
    status_byte = payload[20]
    arm_state = (status_byte >> 5) & 0x03
    arm_states = {0: "desarmada", 1: "parcial", 3: "armada"}
    battery_names = {1: "morta", 2: "baixa", 3: "média", 4: "cheia"}

    print("Estrutura do frame:")
    print(f"  frame[0:2]   destino: {hex_bytes(frame[0:2])}")
    print(f"  frame[2:4]   origem: {hex_bytes(frame[2:4])}")
    print(f"  frame[4:6]   tamanho declarado: {int.from_bytes(frame[4:6], 'big')} bytes")
    print(f"  frame[6:8]   comando: 0x{frame[6:8].hex().upper()}")
    print(f"  frame[8:{8 + payload_length}] payload: {payload_length} bytes")
    print(f"  frame[{len(frame) - 1}]     checksum: {frame[-1]:02X} ({'válido' if frame_is_valid(frame) else 'inválido'})")

    print("Payload de status:")
    print(f"  payload[0]       modelo: 0x{payload[0]:02X}")
    print(f"  payload[1:4]     firmware: {payload[1]}.{payload[2]}.{payload[3]}")
    print(f"  payload[4:12]    não mapeado: {hex_bytes(payload[4:12])}")
    print_mask_block("zonas habilitadas", payload, 12, 18, 56)
    print(f"  payload[19]      não mapeado: {payload[19]:02X}")
    print(
        f"  payload[20]      estado global: {status_byte:02X} "
        f"(arme={arm_states.get(arm_state, 'desconhecido')}, "
        f"zonas_firing={'sim' if status_byte & 0x08 else 'não'}, "
        f"zonas_fechadas={'sim' if status_byte & 0x04 else 'não'}, "
        f"sirene={'sim' if status_byte & 0x02 else 'não'})"
    )

    print("  payload[21:37]    partições:")
    for index, value in enumerate(payload[21:37]):
        flags = []
        if value & 0x80:
            flags.append("habilitada")
        if value & 0x40:
            flags.append("stay")
        if value & 0x08:
            flags.append("disparada")
        if value & 0x04:
            flags.append("disparando")
        if value & 0x01:
            flags.append("armada")
        print(f"    P{index}: {value:02X} ({', '.join(flags) if flags else 'inativa/desarmada'})")

    print(f"  payload[37]      não mapeado: {payload[37]:02X}")
    print_mask_block("zonas abertas", payload, 38, 44, 56)
    print(f"  payload[45]      não mapeado: {payload[45]:02X}")
    print_mask_block("zonas violadas", payload, 46, 52, 56)
    print(f"  payload[53]      não mapeado: {payload[53]:02X}")
    print_mask_block("zonas em bypass", payload, 54, 61, 56)
    print(f"  payload[62:71]   não mapeado: {hex_bytes(payload[62:71])}")
    print(f"  payload[71]      tamper da central: {payload[71]:02X} ({'detectado' if payload[71] & 0x02 else 'normal'})")
    print(f"  payload[72:89]   não mapeado: {hex_bytes(payload[72:89])}")
    print_mask_block("tamper das zonas", payload, 89, 95, 56)
    print(f"  payload[96:105]  não mapeado: {hex_bytes(payload[96:105])}")
    print_mask_block("bateria baixa nas zonas", payload, 105, 111, 56)
    print(f"  payload[112:134] não mapeado: {hex_bytes(payload[112:134])}")
    print(f"  payload[134]     bateria da central: {payload[134]:02X} ({battery_names.get(payload[134], 'desconhecida')})")
    print(f"  payload[135:143] não mapeado: {hex_bytes(payload[135:143])}")


def status_to_dict(status: PanelStatus) -> dict:
    return {
        "model": f"0x{status.model:02X}",
        "firmware": status.version,
        "state": status.state,
        "siren_live": status.siren_live,
        "zones_firing": status.zones_firing,
        "zones_closed": status.zones_closed,
        "battery": status.battery,
        "tamper": status.tamper,
        "partitions": [asdict(partition) for partition in status.partitions],
        "zones": [asdict(zone) for zone in status.zones],
    }


def print_status(status: PanelStatus) -> None:
    print(f"Modelo: 0x{status.model:02X}")
    print(f"Firmware: {status.version}")
    print(f"Estado: {status.state}")
    print(f"Sirene: {'ativa' if status.siren_live else 'inativa'}")
    print(f"Zonas acionadas: {'sim' if status.zones_firing else 'não'}")
    print(f"Zonas fechadas: {'sim' if status.zones_closed else 'não'}")
    print(f"Bateria: {status.battery}")
    print(f"Tamper: {'detectado' if status.tamper else 'normal'}")

    print("Partições:")
    for partition in status.partitions:
        state = "armada" if partition.armed else "desarmada"
        flags = []
        if partition.stay:
            flags.append("stay")
        if partition.firing:
            flags.append("disparando")
        if partition.fired:
            flags.append("disparada")
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(f"  P{partition.index}: {state}{suffix}")

    print("Zonas:")
    for zone in status.zones:
        flags = []
        if zone.open:
            flags.append("aberta")
        if zone.violated:
            flags.append("violada")
        if zone.bypassed:
            flags.append("bypass")
        if zone.tamper:
            flags.append("tamper")
        if zone.low_battery:
            flags.append("bateria baixa")
        print(f"  Z{zone.number}: {', '.join(flags) if flags else 'normal'}")


def parse_partition(value: str) -> int:
    if value.lower() == "all":
        return ALL_PARTITIONS
    try:
        partition = int(value)
    except ValueError as exc:
        raise SystemExit("A partição deve ser um número de 1 a 15 ou 'all'.") from exc
    if not 1 <= partition <= 15:
        raise SystemExit("A partição deve estar entre 1 e 15.")
    return partition


def confirm_action(description: str) -> None:
    answer = input(f"ATENÇÃO: {description}. Digite 'CONFIRMAR' para continuar: ")
    if answer != "CONFIRMAR":
        raise SystemExit("Operação cancelada.")


def print_dry_run(packet: bytes) -> None:
    print("Modo simulação: nenhum comando foi enviado.")
    print_command_frame("Requisição", packet)
    print("Use --execute para enviar o comando, após revisar o alvo.")


def print_auth_trace(frame: bytes, payload: list[int]) -> None:
    masked_payload = [f"{payload[0]:02X}"] + ["**"] * 6 + [f"{payload[7]:02X}"]
    frame_tokens = [f"{value:02X}" for value in frame]
    frame_tokens[9:15] = ["**"] * 6
    print("Autenticação:")
    print("  comando: 0xF0F0 AUTHORIZE")
    print(f"  device_type: 0x{payload[0]:02X}")
    print(f"  password: {' '.join(masked_payload[1:7])}")
    print(f"  software_version: 0x{payload[7]:02X}")
    print(f"  checksum: {frame[-1]:02X} (calculado sobre os bytes reais)")
    print(f"  frame mascarado: {' '.join(frame_tokens)}")


def print_command_frame(label: str, frame: bytes) -> None:
    command = frame[6:8] if len(frame) >= 8 else b""
    payload = frame[8:-1] if len(frame) >= 9 else b""
    print(f"{label}:")
    print(f"  comando: 0x{command.hex().upper() if command else '??'}")
    print(f"  payload: {hex_bytes(payload) if payload else '(vazio)'}")
    print(f"  checksum: {'válido' if frame_is_valid(frame) else 'inválido'}")
    print(f"  frame: {hex_bytes(frame)}")


def describe_request(command: bytes, payload: bytes) -> str:
    command_hex = f"0x{command.hex().upper()}"
    if command == ARM_COMMAND and len(payload) >= 2:
        operations = {0: "desarmar", 1: "armar away", 2: "armar stay"}
        partition = "todas (0xFF)" if payload[0] == 0xFF else str(payload[0])
        return f"{command_hex} SYSTEM_ARM_DISARM: partição={partition}, operação={operations.get(payload[1], 'desconhecida')}"
    if command == BYPASS_COMMAND and len(payload) >= 2:
        return f"{command_hex} BYPASS_ZONE: zona_index={payload[0]}, bypass={'ativar' if payload[1] else 'remover'}"
    if command == PANIC_COMMAND and payload:
        names = {0: "silencioso", 1: "audível", 2: "fogo", 3: "médico"}
        return f"{command_hex} PANIC_ALARM: tipo={names.get(payload[0], 'desconhecido')}"
    if command == PGM_COMMAND and len(payload) >= 2:
        return f"{command_hex} PGM_ON_OFF: índice={payload[0]}, estado={'ligado' if payload[1] else 'desligado'}"
    names = {
        STATUS_COMMAND: "ALARM_PANEL_STATUS",
        KEEP_ALIVE_COMMAND: "KEEP_ALIVE",
        GET_MAC_COMMAND: "GET_MAC",
        SIREN_OFF_COMMAND: "TURN_OFF_SIREN",
    }
    return f"{command_hex} {names.get(command, 'comando desconhecido')}"


def print_command_response(frame: bytes) -> None:
    response_command = f"0x{frame[6:8].hex().upper()}" if len(frame) >= 8 else "desconhecido"
    payload = frame[8:-1] if len(frame) >= 9 else b""
    result = {0xF0FE: "ACK — aceito", 0xF0FD: "NACK — rejeitado"}.get(
        int.from_bytes(frame[6:8], "big") if len(frame) >= 8 else -1,
        "resposta específica",
    )
    print(f"Resposta: {response_command} ({result})")
    print(f"  payload: {hex_bytes(payload) if payload else '(vazio)'}")
    if response_command == "0xF0FD" and payload:
        print(f"  erro: 0x{payload[0]:02X} ({payload[0]})")
    print(f"  checksum: {'válido' if frame_is_valid(frame) else 'inválido'}")
    print(f"  frame: {hex_bytes(frame)}")


def decode_mac_response(frame: bytes) -> str | None:
    if len(frame) < 16:
        return None
    mac_bytes = frame[9:-1]
    if len(mac_bytes) != 6:
        return None
    return ":".join(f"{value:02X}" for value in mac_bytes)


async def connect_client(args: argparse.Namespace) -> DiagnosticClient:
    host = require_host(args)
    return DiagnosticClient(host, args.port, read_password(), trace_auth=args.trace_auth)


async def run_read_command(args: argparse.Namespace) -> None:
    client = await connect_client(args)
    status, frame, request = await client.get_status_frame()

    if args.command == "raw-status":
        print(f"Comando da consulta: {describe_request(STATUS_COMMAND, b'')}")
        print_command_frame("Requisição", request)
        print_status_frame_details(frame)
        print("\nFrame completo:")
        print(frame.hex(" "))
        return

    if args.json:
        print(json.dumps(status_to_dict(status), indent=2, ensure_ascii=False))
    else:
        print_status(status)


async def run_probe(args: argparse.Namespace) -> None:
    if args.command == "mac":
        command = GET_MAC_COMMAND
        payload = bytes([0x00])
    else:
        command = KEEP_ALIVE_COMMAND
        payload = b""

    packet = Amt8000Client("127.0.0.1", 9009, "")._packet(list(command), list(payload))
    if not args.execute:
        print(f"Comando: {describe_request(command, payload)}")
        print_dry_run(packet)
        return

    confirm_action(f"enviar {args.command.upper()} para a central")
    client = await connect_client(args)
    print(f"Comando: {describe_request(command, payload)}")
    print_command_frame("Requisição", packet)
    response = await client.send_command_frame(command, payload)
    print_command_response(response)
    if args.command == "mac":
        mac = decode_mac_response(response)
        print(f"MAC: {mac if mac else 'não identificado na resposta'}")


async def run_watch(args: argparse.Namespace) -> None:
    client = await connect_client(args)
    count = 0
    while args.count == 0 or count < args.count:
        status, _, _ = await client.get_status_frame()
        print_status(status)
        print("-" * 60)
        count += 1
        if args.count == 0 or count < args.count:
            await asyncio.sleep(args.interval)


async def run_control(args: argparse.Namespace) -> None:
    partition = parse_partition(args.partition)
    if args.command == "disarm":
        subcommand = 0x00
    else:
        subcommand = {"away": 0x01, "stay": 0x02}[args.mode]
    payload = [partition, subcommand]
    packet = Amt8000Client("127.0.0.1", 9009, "")._packet(list(ARM_COMMAND), payload)

    if not args.execute:
        print(f"Comando: {describe_request(ARM_COMMAND, bytes(payload))}")
        print_dry_run(packet)
        return

    target = "todas as partições" if partition == ALL_PARTITIONS else f"a partição {partition}"
    mode = getattr(args, "mode", "disarm")
    confirm_action(f"{args.command.upper()} {target} ({mode})")
    client = await connect_client(args)
    print(f"Comando: {describe_request(ARM_COMMAND, bytes(payload))}")
    print_command_frame("Requisição", packet)
    response = await client.send_command_frame(ARM_COMMAND, bytes(payload))
    print_command_response(response)


async def run_bypass(args: argparse.Namespace) -> None:
    if any(not 1 <= zone <= 56 for zone in args.zone):
        raise SystemExit("As zonas devem estar entre 1 e 56.")

    flag = 0x00 if args.clear else 0x01
    zero_based_zones = [zone - 1 for zone in args.zone]
    packets = [
        Amt8000Client("127.0.0.1", 9009, "")._packet(list(BYPASS_COMMAND), [zone, flag])
        for zone in zero_based_zones
    ]
    if not args.execute:
        for zone, packet in zip(args.zone, packets):
            print(f"Zona {zone}:")
            print(f"Comando: {describe_request(BYPASS_COMMAND, bytes([zone - 1, flag]))}")
            print_dry_run(packet)
        return

    action = "REMOVER BYPASS" if args.clear else "ATIVAR BYPASS"
    confirm_action(f"{action} nas zonas {', '.join(map(str, args.zone))}")
    client = await connect_client(args)
    for zone, zone_index, packet in zip(args.zone, zero_based_zones, packets):
        payload = bytes([zone_index, flag])
        print(f"Zona {zone}: {describe_request(BYPASS_COMMAND, payload)}")
        print_command_frame("Requisição", packet)
        response = await client.send_command_frame(BYPASS_COMMAND, payload)
        print_command_response(response)


async def run_extended_control(args: argparse.Namespace) -> None:
    if args.command == "panic":
        panic_types = {"silent": 0, "audible": 1, "fire": 2, "medical": 3}
        command = PANIC_COMMAND
        payload = bytes([panic_types[args.type]])
        description = f"pânico {args.type}"
    elif args.command == "siren-off":
        command = SIREN_OFF_COMMAND
        payload = b""
        description = "desligar a sirene"
    else:
        if not 0 <= args.index <= 7:
            raise SystemExit("O índice PGM deve estar entre 0 e 7.")
        command = PGM_COMMAND
        payload = bytes([args.index, 0x01 if args.state == "on" else 0x00])
        description = f"PGM {args.index} {args.state}"

    packet = Amt8000Client("127.0.0.1", 9009, "")._packet(list(command), list(payload))
    print(f"Comando: {describe_request(command, payload)}")
    if not args.execute:
        print_dry_run(packet)
        return

    confirm_action(description)
    client = await connect_client(args)
    print_command_frame("Requisição", packet)
    response = await client.send_command_frame(command, payload)
    print_command_response(response)


async def async_main(args: argparse.Namespace) -> None:
    if args.command in {"status", "raw-status"}:
        await run_read_command(args)
    elif args.command in {"mac", "keep-alive"}:
        await run_probe(args)
    elif args.command in {"panic", "siren-off", "pgm"}:
        await run_extended_control(args)
    elif args.command == "watch":
        await run_watch(args)
    elif args.command in {"arm", "disarm"}:
        await run_control(args)
    elif args.command == "bypass":
        await run_bypass(args)


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nMonitoramento interrompido.")
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
