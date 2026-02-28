"""
Formato unificado GNI para o grupo Telegram.
Todo post do desk vira um resumo neste formato (português).
"""
from desk.types import parse_desk_type, DeskType

# Saudação por horário (slot)
_GREETING_BY_TYPE = {
    DeskType.OVERNIGHT_GLOBAL_0500: "Bom dia",
    DeskType.PREMARKET_BR_0800: "Bom dia",
    DeskType.PANORAMA_0900: "Bom dia",
    DeskType.THREAT_MONITOR_1130: "Boa tarde",
    DeskType.ALERTA_TATICO_1200: "Boa tarde",
    DeskType.FLOW_1330: "Boa tarde",
    DeskType.REALTIME_VOL_1530: "Boa tarde",
    DeskType.RISK_MATRIX_1800: "Boa tarde",
    DeskType.EXEC_SUMMARY_2030: "Boa noite",
    DeskType.OVERNIGHT_WATCH_2300: "Boa noite",
}

_HEADER = """🌐 GLOBAL NEWS INTEL (GNI)

🧠 Desk de Inteligência Estratégica

"""

_FOOTER = """

📡 Atualizações estratégicas a qualquer momento.
Fiquem atentos.

— Equipe GNI"""


def _greeting_for(desk_type: str) -> str:
    try:
        dt = parse_desk_type(desk_type)
        return _GREETING_BY_TYPE.get(dt, "Boa noite")
    except Exception:
        return "Boa noite"


def format_gni_desk_post(desk_type: str, composed_text: str) -> str:
    """
    Envolve o texto composto no formato oficial GNI para o Telegram.
    Sempre em português: cabeçalho, saudação, corpo, rodapé.
    """
    if not (composed_text and composed_text.strip()):
        composed_text = "Monitorando. Sem sinal confirmado no momento."

    greeting = _greeting_for(desk_type)
    greeting_line = f"{greeting}, operadores.\n\n"

    return _HEADER + greeting_line + composed_text.strip() + _FOOTER
