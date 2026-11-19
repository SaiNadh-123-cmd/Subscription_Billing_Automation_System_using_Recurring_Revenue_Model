"""
Pure-Python PDF Invoice Generator built with fpdf2.
Constructs formatted SaaS billing invoices entirely in memory without compiled C dependencies.
"""

from datetime import datetime
from typing import Optional
from fpdf import FPDF


def _safe_text(value: Optional[str]) -> str:
    """Sanitize strings to Latin-1 for built-in core PDF fonts."""
    if value is None:
        return ""
    return str(value).encode("latin-1", "replace").decode("latin-1")


def _fmt_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d")


def _fmt_currency(amount: float) -> str:
    return f"${float(amount):,.2f}"


class _InvoicePDF(FPDF):
    def footer(self) -> None:
        self.set_y(-20)
        self.set_draw_color(226, 232, 240)
        self.line(15, self.get_y(), 195, self.get_y())
        self.ln(4)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 116, 139)
        self.cell(
            0,
            5,
            "SubBilling Enterprise Platform  |  Automated Recurring Billing Statement  |  billing@subbilling.io",
            align="C",
        )


def generate_invoice_pdf(
    *,
    invoice_number: str,
    status: str,
    invoice_date: datetime,
    due_date: datetime,
    customer_name: str,
    customer_email: str,
    customer_company: Optional[str],
    customer_id: int,
    subscription_id: int,
    plan_name: str,
    billing_interval: str,
    subtotal: float,
    tax_amount: float,
    total_amount: float,
    cycle_start_date: Optional[datetime] = None,
    cycle_end_date: Optional[datetime] = None,
) -> bytes:
    """
    Constructs a cleanly styled PDF invoice in memory and returns raw PDF bytes.
    """
    pdf = _InvoicePDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.add_page()

    # --------------------------------------------------
    # 1. Top Brand Header Banner
    # --------------------------------------------------
    pdf.set_fill_color(15, 23, 42)  # Slate-900 dark header
    pdf.rect(0, 0, 210, 40, style="F")

    # Company Name & Tagline
    pdf.set_xy(15, 10)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(100, 9, "SubBilling", ln=1)

    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(100, 5, "Enterprise Subscription & Recurring Revenue Platform", ln=1)

    pdf.set_x(15)
    pdf.cell(100, 5, "500 Cloud Park Ave, Suite 400  |  billing@subbilling.io", ln=0)

    # Right Side: INVOICE Title + Number
    pdf.set_xy(120, 10)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(129, 140, 248)  # Indigo-400 accent
    pdf.cell(75, 10, "INVOICE", align="R", ln=1)

    pdf.set_x(120)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(241, 245, 249)
    pdf.cell(75, 6, _safe_text(invoice_number), align="R", ln=1)

    # Status Pill
    status_upper = _safe_text(status).upper()
    if status_upper == "PAID":
        pill_fill = (16, 185, 129)  # Emerald
    elif status_upper == "UNPAID":
        pill_fill = (245, 158, 11)  # Amber
    else:
        pill_fill = (100, 116, 139)  # Slate

    pdf.set_fill_color(*pill_fill)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    pill_w = 28
    pdf.set_xy(195 - pill_w, 28)
    pdf.cell(pill_w, 6, status_upper, align="C", fill=True)

    # --------------------------------------------------
    # 2. Bill To & Invoice Metadata Columns
    # --------------------------------------------------
    y_info = 50
    pdf.set_xy(15, y_info)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(79, 70, 229)  # Indigo-600
    pdf.cell(90, 6, "BILL TO", ln=1)

    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(90, 6, _safe_text(customer_name), ln=1)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    if customer_company:
        pdf.set_x(15)
        pdf.cell(90, 5.5, _safe_text(customer_company), ln=1)
    pdf.set_x(15)
    pdf.cell(90, 5.5, _safe_text(customer_email), ln=1)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(90, 5.5, f"Customer ID: #{customer_id}", ln=1)

    # Right Metadata Box
    meta_x = 120
    pdf.set_xy(meta_x, y_info)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(79, 70, 229)
    pdf.cell(75, 6, "INVOICE DETAILS", align="R", ln=1)

    meta_rows = [
        ("Invoice Date:", _fmt_date(invoice_date)),
        ("Due Date:", _fmt_date(due_date)),
        ("Subscription ID:", f"#{subscription_id}"),
        ("Payment Status:", status_upper),
    ]
    if cycle_start_date and cycle_end_date:
        meta_rows.append(("Billing Period:", f"{_fmt_date(cycle_start_date)} to {_fmt_date(cycle_end_date)}"))

    curr_y = y_info + 6
    for label, val in meta_rows:
        pdf.set_xy(meta_x, curr_y)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(35, 5.5, label, align="L")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(40, 5.5, _safe_text(val), align="R")
        curr_y += 5.5

    # Divider Line
    table_top = max(pdf.get_y(), curr_y) + 12
    pdf.set_draw_color(226, 232, 240)
    pdf.line(15, table_top - 4, 195, table_top - 4)

    # --------------------------------------------------
    # 3. Line Items Table
    # --------------------------------------------------
    pdf.set_xy(15, table_top)
    pdf.set_fill_color(241, 245, 249)  # Slate-100
    pdf.set_text_color(51, 65, 85)
    pdf.set_font("Helvetica", "B", 9)

    col_desc = 85
    col_interval = 30
    col_qty = 15
    col_unit = 25
    col_amt = 25

    pdf.cell(col_desc, 9, "  DESCRIPTION / PLAN", border=0, fill=True)
    pdf.cell(col_interval, 9, "INTERVAL", border=0, align="C", fill=True)
    pdf.cell(col_qty, 9, "QTY", border=0, align="C", fill=True)
    pdf.cell(col_unit, 9, "UNIT PRICE", border=0, align="R", fill=True)
    pdf.cell(col_amt, 9, "AMOUNT  ", border=0, align="R", fill=True, ln=1)

    # Row 1: Plan Subscription Charge
    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    item_desc = f"{_safe_text(plan_name)} Subscription"
    pdf.cell(col_desc, 10, f"  {item_desc}", border="B")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(col_interval, 10, _safe_text(billing_interval).capitalize(), border="B", align="C")
    pdf.cell(col_qty, 10, "1", border="B", align="C")
    pdf.cell(col_unit, 10, _fmt_currency(subtotal), border="B", align="R")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(col_amt, 10, f"{_fmt_currency(subtotal)}  ", border="B", align="R", ln=1)

    # --------------------------------------------------
    # 4. Financial Totals Box
    # --------------------------------------------------
    pdf.ln(8)
    totals_x = 120
    label_w = 40
    val_w = 35

    pdf.set_x(totals_x)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(label_w, 7, "Subtotal:", align="L")
    pdf.set_text_color(15, 23, 42)
    pdf.cell(val_w, 7, _fmt_currency(subtotal), align="R", ln=1)

    pdf.set_x(totals_x)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(label_w, 7, "Standard Tax (18%):", align="L")
    pdf.set_text_color(15, 23, 42)
    pdf.cell(val_w, 7, _fmt_currency(tax_amount), align="R", ln=1)

    pdf.ln(2)
    pdf.set_x(totals_x)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(label_w, 10, "  TOTAL DUE", fill=True, align="L")
    pdf.cell(val_w, 10, f"{_fmt_currency(total_amount)}  ", fill=True, align="R", ln=1)

    # --------------------------------------------------
    # 5. Notes / Payment Terms Block
    # --------------------------------------------------
    pdf.ln(16)
    pdf.set_x(15)
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(180, 7, "  PAYMENT TERMS & NOTES", border="LTR", fill=True, ln=1)

    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(100, 116, 139)
    note_text = (
        "  Please remit payment by the due date listed above referencing your Invoice Number. "
        "For billing inquiries or plan adjustments, contact billing@subbilling.io."
    )
    pdf.multi_cell(180, 5.5, note_text, border="LBR", fill=True)

    return bytes(pdf.output())
