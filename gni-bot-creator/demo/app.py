"""
GNI Agent Demo — WhatsApp automation + operations dashboard.
Portfolio/demo showcasing: ingestion → classification → message drafts → delivery → outcomes.
ALL DATA IS MOCKED. No external APIs or network calls.

Run: streamlit run demo/app.py
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

# --- Page config ---
st.set_page_config(
    page_title="GNI Agent Demo",
    page_icon="📱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Constants ---
SOURCES = ["RSS", "Manual", "CRM", "Web"]
CATEGORIES = ["Lead", "News", "Support", "Finance", "Other"]
PRIORITIES = ["High", "Med", "Low"]
CHANNELS = ["WhatsApp", "Telegram", "Email"]
AUDIENCES = ["customer", "prospect", "internal"]
DRAFT_STATUSES = ["DRAFT", "APPROVED", "SENT"]
DELIVERY_STATUSES = ["SENT", "FAILED"]
OUTCOMES = ["REPLIED", "IGNORED", "CONVERTED", "ESCALATED"]
BLOCKED_WORDS = ["urgent", "asap", "free money", "click here"]
MAX_RETRIES = 3

# --- Mock data generators ---


@dataclass
class IngestionItem:
    item_id: str
    source: str
    title: str
    content_snippet: str
    received_at: datetime


@dataclass
class ClassificationResult:
    item_id: str
    category: str
    priority: str
    confidence: float
    tags: list[str]
    rationale: str


@dataclass
class MessageDraft:
    draft_id: str
    item_id: str
    channel: str
    audience: str
    message_text: str
    tone: str
    created_at: datetime
    status: str


@dataclass
class DeliveryLog:
    delivery_id: str
    draft_id: str
    channel: str
    recipient_group: str
    status: str
    retry_count: int
    delivered_at: datetime | None
    error: str | None


@dataclass
class Outcome:
    item_id: str
    outcome: str
    time_to_reply_min: int | None
    notes: str


def _random_id(prefix: str) -> str:
    return f"{prefix}_{random.randint(10000, 99999)}"


def _random_datetime(hours_ago: int = 72) -> datetime:
    return datetime.now() - timedelta(hours=random.randint(0, hours_ago))


def generate_ingestion_batch(count: int = 5) -> list[IngestionItem]:
    titles = [
        "New lead from website form — João Silva",
        "Support ticket #4521 — payment issue",
        "News: Market update Q4 summary",
        "Finance alert: Budget review request",
        "CRM sync: 3 new prospects added",
        "RSS: Tech industry news roundup",
        "Manual: Follow-up reminder for ABC Corp",
        "Web: Contact form — Maria Santos",
    ]
    snippets = [
        "Customer reported issue with payment gateway. Needs urgent attention.",
        "Quarterly results show 15% growth. Full report attached.",
        "Interested in enterprise plan. Requested demo for next week.",
        "Question about API limits and pricing tiers.",
        "News article about AI automation trends in customer service.",
        "Budget approval pending. Please review by EOD.",
    ]
    items = []
    for _ in range(count):
        items.append(
            IngestionItem(
                item_id=_random_id("item"),
                source=random.choice(SOURCES),
                title=random.choice(titles),
                content_snippet=random.choice(snippets),
                received_at=_random_datetime(48),
            )
        )
    return items


def generate_classifications(items: list[IngestionItem]) -> list[ClassificationResult]:
    rationales = [
        "Keywords 'lead' and 'demo' indicate sales intent.",
        "Support-related language and ticket reference.",
        "Financial terms and budget mention.",
        "News-style content from RSS source.",
        "Generic inquiry; default to Other.",
    ]
    results = []
    for it in items:
        cat = random.choice(CATEGORIES)
        prio = random.choice(PRIORITIES)
        conf = round(random.uniform(0.75, 0.99), 2)
        tags = random.sample(["sales", "billing", "urgent", "follow-up", "demo"], k=random.randint(1, 3))
        results.append(
            ClassificationResult(
                item_id=it.item_id,
                category=cat,
                priority=prio,
                confidence=conf,
                tags=tags,
                rationale=random.choice(rationales),
            )
        )
    return results


def generate_drafts_for_item(
    item_id: str, item_title: str, classification: ClassificationResult
) -> list[MessageDraft]:
    tones = ["formal", "short", "friendly"]
    templates = [
        f"Dear valued contact,\n\nWe received your inquiry regarding \"{item_title[:40]}...\". Our team will review and respond within 24 hours.\n\nBest regards,\nGNI Team",
        f"Hi! Thanks for reaching out. We're on it — expect a reply soon. 📱",
        f"Hey! Got your message about {item_title[:30]}. We'll get back to you ASAP. Cheers!",
    ]
    drafts = []
    for i, (tone, text) in enumerate(zip(tones, templates)):
        drafts.append(
            MessageDraft(
                draft_id=_random_id("draft"),
                item_id=item_id,
                channel=random.choice(CHANNELS),
                audience=random.choice(AUDIENCES),
                message_text=text,
                tone=tone,
                created_at=_random_datetime(24),
                status="DRAFT",
            )
        )
    return drafts


def generate_delivery_logs(drafts: list[MessageDraft]) -> list[DeliveryLog]:
    logs = []
    for d in drafts:
        if d.status == "SENT":
            logs.append(
                DeliveryLog(
                    delivery_id=_random_id("del"),
                    draft_id=d.draft_id,
                    channel=d.channel,
                    recipient_group=random.choice(["sales", "support", "clients"]),
                    status="SENT",
                    retry_count=0,
                    delivered_at=_random_datetime(12),
                    error=None,
                )
            )
    return logs


def generate_outcomes(items: list[IngestionItem]) -> list[Outcome]:
    outcomes = []
    for it in items[: len(items) // 2]:
        outcomes.append(
            Outcome(
                item_id=it.item_id,
                outcome=random.choice(OUTCOMES),
                time_to_reply_min=random.randint(5, 120) if random.random() > 0.3 else None,
                notes=random.choice(["Positive response", "No reply yet", "Escalated to sales"]) if random.random() > 0.5 else "",
            )
        )
    return outcomes


# --- Guardrails ---


def check_blocked_words(text: str) -> list[str]:
    """Return list of blocked words found in text."""
    found = []
    lower = text.lower()
    for w in BLOCKED_WORDS:
        if w in lower:
            found.append(w)
    return found


def requires_approval(priority: str, guardrails: dict) -> bool:
    return guardrails.get("approve_high_priority", True) and priority == "High"


def get_max_retries(guardrails: dict) -> int:
    return guardrails.get("max_retries", MAX_RETRIES)


# --- State initialization ---


def init_session_state() -> None:
    if "ingestion" not in st.session_state:
        st.session_state.ingestion = []
    if "classifications" not in st.session_state:
        st.session_state.classifications = []
    if "drafts" not in st.session_state:
        st.session_state.drafts = []
    if "delivery_logs" not in st.session_state:
        st.session_state.delivery_logs = []
    if "outcomes" not in st.session_state:
        st.session_state.outcomes = []
    if "guardrails" not in st.session_state:
        st.session_state.guardrails = {
            "blocked_words": BLOCKED_WORDS,
            "approve_high_priority": True,
            "max_retries": MAX_RETRIES,
        }
    # Seed initial batch if empty
    if not st.session_state.ingestion:
        _add_mock_batch()


def _add_mock_batch() -> None:
    items = generate_ingestion_batch(5)
    classifications = generate_classifications(items)
    drafts = []
    for it, cl in zip(items, classifications):
        dlist = generate_drafts_for_item(it.item_id, it.title, cl)
        # Mark 1 draft as SENT per batch for demo
        if dlist and random.random() > 0.5:
            dlist[0].status = "SENT"
        drafts.extend(dlist)
    delivery_logs = []
    sent_drafts = [d for d in drafts if d.status == "SENT"]
    for d in sent_drafts[:3]:
        delivery_logs.append(
            DeliveryLog(
                delivery_id=_random_id("del"),
                draft_id=d.draft_id,
                channel=d.channel,
                recipient_group=random.choice(["sales", "support", "clients"]),
                status="SENT",
                retry_count=0,
                delivered_at=_random_datetime(12),
                error=None,
            )
        )
    outcomes = generate_outcomes(items)
    st.session_state.ingestion.extend(items)
    st.session_state.classifications.extend(classifications)
    st.session_state.drafts.extend(drafts)
    st.session_state.delivery_logs.extend(delivery_logs)
    st.session_state.outcomes.extend(outcomes)


# --- KPI helpers ---


def _items_today(items: list[IngestionItem]) -> int:
    today = datetime.now().date()
    return sum(1 for i in items if i.received_at.date() == today)


def _high_priority_count(classifications: list[ClassificationResult]) -> int:
    return sum(1 for c in classifications if c.priority == "High")


def _messages_sent(delivery_logs: list[DeliveryLog]) -> int:
    return sum(1 for d in delivery_logs if d.status == "SENT")


def _reply_rate(outcomes: list[Outcome]) -> float:
    if not outcomes:
        return 0.0
    replied = sum(1 for o in outcomes if o.outcome == "REPLIED")
    return round(replied / len(outcomes) * 100, 1)


def _avg_time_to_reply(outcomes: list[Outcome]) -> float:
    times = [o.time_to_reply_min for o in outcomes if o.time_to_reply_min]
    return round(sum(times) / len(times), 1) if times else 0.0


# --- App ---


def main() -> None:
    init_session_state()
    ingestion = st.session_state.ingestion
    classifications = st.session_state.classifications
    drafts = st.session_state.drafts
    delivery_logs = st.session_state.delivery_logs
    outcomes = st.session_state.outcomes
    guardrails = st.session_state.guardrails

    st.header("📱 GNI Agent — WhatsApp Automation Demo")
    st.caption("Portfolio demo • All data mocked • No external APIs")

    # --- Sidebar ---
    with st.sidebar:
        st.header("Filters")
        cat_filter = st.multiselect("Category", CATEGORIES, default=[])
        prio_filter = st.multiselect("Priority", PRIORITIES, default=[])
        chan_filter = st.multiselect("Channel", CHANNELS, default=[])
        date_min = st.date_input("From date", datetime.now().date() - timedelta(days=7))
        date_max = st.date_input("To date", datetime.now().date())

        st.divider()
        if st.button("🔄 Generate new batch", use_container_width=True):
            _add_mock_batch()
            st.rerun()

        st.divider()
        st.subheader("Guardrails")
        guardrails["approve_high_priority"] = st.checkbox(
            "Require approval for High priority", value=guardrails["approve_high_priority"]
        )
        guardrails["max_retries"] = st.number_input("Max retries on failure", 1, 10, guardrails["max_retries"])
        st.text("Blocked words: " + ", ".join(BLOCKED_WORDS[:3]) + "...")

    # --- KPI row ---
    items_today = _items_today(ingestion)
    high_prio = _high_priority_count(classifications)
    sent = _messages_sent(delivery_logs)
    reply_rate = _reply_rate(outcomes)
    avg_ttr = _avg_time_to_reply(outcomes)

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    kpi1.metric("Items today", items_today)
    kpi2.metric("High priority", high_prio)
    kpi3.metric("Messages sent", sent)
    kpi4.metric("Reply rate %", f"{reply_rate}%")
    kpi5.metric("Avg time-to-reply (min)", avg_ttr)

    st.divider()

    # --- Apply filters ---
    def _match_item(item: IngestionItem) -> bool:
        cl = next((c for c in classifications if c.item_id == item.item_id), None)
        if not cl:
            return True
        if cat_filter and cl.category not in cat_filter:
            return False
        if prio_filter and cl.priority not in prio_filter:
            return False
        if date_min and item.received_at.date() < date_min:
            return False
        if date_max and item.received_at.date() > date_max:
            return False
        return True

    filtered_items = [i for i in ingestion if _match_item(i)]

    # --- Ingestion table ---
    st.subheader("Ingestion Queue")
    if filtered_items:
        df_ing = pd.DataFrame(
            [
                {
                    "item_id": i.item_id,
                    "source": i.source,
                    "title": i.title,
                    "content_snippet": i.content_snippet[:60] + "...",
                    "received_at": i.received_at.strftime("%Y-%m-%d %H:%M"),
                }
                for i in filtered_items
            ]
        )
        st.dataframe(df_ing, use_container_width=True, hide_index=True)
    else:
        st.info("No items match filters. Click 'Generate new batch' in sidebar.")

    st.divider()

    # --- Classifier table ---
    st.subheader("Classifier Results")
    filtered_cls = [c for c in classifications if c.item_id in {i.item_id for i in filtered_items}]
    if filtered_cls:
        df_cls = pd.DataFrame(
            [
                {
                    "item_id": c.item_id,
                    "category": c.category,
                    "priority": c.priority,
                    "confidence": c.confidence,
                    "tags": ", ".join(c.tags),
                    "rationale": c.rationale,
                }
                for c in filtered_cls
            ]
        )
        st.dataframe(df_cls, use_container_width=True, hide_index=True)
    else:
        st.info("No classifications for filtered items.")

    st.divider()

    # --- Message Studio ---
    st.subheader("Message Studio")
    item_options = {f"{i.item_id} — {i.title[:40]}...": i.item_id for i in filtered_items}
    selected_label = st.selectbox("Select item to view drafts", ["(Choose one)"] + list(item_options.keys()))

    if selected_label and selected_label != "(Choose one)":
        selected_item_id = item_options[selected_label]
        item_drafts = [d for d in drafts if d.item_id == selected_item_id]
        cl = next((c for c in classifications if c.item_id == selected_item_id), None)
        priority = cl.priority if cl else "Med"

        # Guardrail: High priority requires approval
        if requires_approval(priority, guardrails):
            st.warning("⚠️ High priority item — requires approval before sending (Guardrail)")

        for d in item_drafts[:3]:
            blocked = check_blocked_words(d.message_text)
            with st.container():
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.text(f"Tone: {d.tone} | Channel: {d.channel} | Status: {d.status}")
                    st.text_area("Message", value=d.message_text, height=80, key=f"msg_{d.draft_id}", disabled=True)
                    if blocked:
                        st.error(f"Blocked words detected: {', '.join(blocked)} (Guardrail)")
                with col_b:
                    if d.status == "DRAFT" and not blocked:
                        if st.button("Approve & Send", key=f"btn_{d.draft_id}"):
                            d.status = "SENT"
                            st.session_state.delivery_logs.append(
                                DeliveryLog(
                                    delivery_id=_random_id("del"),
                                    draft_id=d.draft_id,
                                    channel=d.channel,
                                    recipient_group=random.choice(["sales", "support", "clients"]),
                                    status="SENT",
                                    retry_count=0,
                                    delivered_at=datetime.now(),
                                    error=None,
                                )
                            )
                            st.session_state.outcomes.append(
                                Outcome(item_id=selected_item_id, outcome="REPLIED", time_to_reply_min=None, notes="")
                            )
                            st.success("Sent!")
                            st.rerun()
                    elif d.status == "SENT":
                        st.success("Sent")

    st.divider()

    # --- Delivery & Outcomes ---
    st.subheader("Delivery & Outcomes")

    col_del, col_out = st.columns(2)

    with col_del:
        st.markdown("**Delivery log**")
        if delivery_logs:
            df_del = pd.DataFrame(
                [
                    {
                        "delivery_id": d.delivery_id,
                        "channel": d.channel,
                        "recipient_group": d.recipient_group,
                        "status": d.status,
                        "retry_count": d.retry_count,
                        "delivered_at": (d.delivered_at.strftime("%Y-%m-%d %H:%M") if d.delivered_at else "-"),
                        "error": d.error or "-",
                    }
                    for d in delivery_logs[-20:]
                ]
            )
            st.dataframe(df_del, use_container_width=True, hide_index=True)
            status_counts = pd.Series([d.status for d in delivery_logs]).value_counts()
            st.bar_chart(status_counts)
        else:
            st.info("No delivery logs yet.")

    with col_out:
        st.markdown("**Outcomes**")
        if outcomes:
            df_out = pd.DataFrame(
                [
                    {
                        "item_id": o.item_id,
                        "outcome": o.outcome,
                        "time_to_reply_min": o.time_to_reply_min or "-",
                        "notes": o.notes or "-",
                    }
                    for o in outcomes[-20:]
                ]
            )
            st.dataframe(df_out, use_container_width=True, hide_index=True)
            outcome_counts = pd.Series([o.outcome for o in outcomes]).value_counts()
            st.bar_chart(outcome_counts)
        else:
            st.info("No outcomes yet.")

    st.divider()

    # --- Guardrails panel ---
    with st.expander("🛡️ Guardrails Panel"):
        st.markdown("**Active rules**")
        st.markdown("- **Blocked words:** " + ", ".join(BLOCKED_WORDS))
        st.markdown(f"- **Require approval for High priority:** {guardrails['approve_high_priority']}")
        st.markdown(f"- **Max retries on failure:** {guardrails['max_retries']}")
        st.caption("Rules affect Message Studio and delivery behavior. Try adding a blocked word to a draft to see the warning.")


if __name__ == "__main__":
    main()
