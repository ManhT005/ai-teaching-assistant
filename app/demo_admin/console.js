"use strict";

const el = (id) => document.getElementById(id);
const show = (id, value) => { el(id).textContent = value; };
const credentials = {
  webhook: "X-Webhook-Token",
  operator: "X-Operator-Token",
  staff: "X-Teacher-Token",
};
const presets = {
  n8n: "Em chạy n8n bị lỗi 401 Unauthorized thì xử lý thế nào?",
  deadline: "Bài tập nộp khi nào?",
  handoff: "Khóa học tiếp theo có giảm học phí không?",
};

let busy = false;
let lastEvent = null;
let activeTicket = null;
let activeFaq = null;
let nextCursor = null;

function notify(message, error = false) {
  show("notice", message);
  el("notice").classList.toggle("error", error);
}

async function api(path, role, body) {
  const token = el(role + "-token").value.trim();
  if (!token) throw new Error("Cần nhập " + role + " token trong phần kết nối.");
  const headers = { [credentials[role]]: token };
  const options = { headers, cache: "no-store", redirect: "error", credentials: "omit" };
  if (body) {
    headers["Content-Type"] = "application/json";
    options.method = "POST";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = typeof data.detail === "string" && /^[A-Z_]+$/.test(data.detail)
      ? " · " + data.detail : "";
    const hint = {
      401: "Kiểm tra token.",
      403: "Tài khoản thiếu quyền cho thao tác này.",
      409: "Tải lại hồ sơ trước khi thử lại.",
      422: "Kiểm tra nội dung và các trường bắt buộc.",
      429: "Giới hạn gửi đã đạt. Thử lại sau.",
    }[response.status] || "Kiểm tra môi trường và thử lại.";
    throw new Error("HTTP " + response.status + code + ". " + hint);
  }
  return data;
}

function action(element, callback) {
  element.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    element.setAttribute("aria-busy", "true");
    for (const role of Object.keys(credentials)) el(role + "-token").disabled = true;
    notify("Đang xử lý…");
    try {
      notify(await callback() || "Đã cập nhật.");
    } catch (error) {
      notify(error instanceof TypeError ? "Không kết nối được. Hãy tải lại dữ liệu trước khi thao tác tiếp." : error.message, true);
    } finally {
      for (const role of Object.keys(credentials)) el(role + "-token").disabled = false;
      element.removeAttribute("aria-busy");
      busy = false;
    }
  });
}

function on(id, callback) {
  action(el(id), callback);
}

function node(tag, value, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value;
  return element;
}

function replaceEmpty(id, message) {
  el(id).replaceChildren(node("p", message, "empty"));
}

function required(id, label) {
  const value = el(id).value.trim();
  if (!value) throw new Error("Cần nhập " + label + ".");
  return value;
}

function command(version, extra = {}) {
  return { expected_version: version, idempotency_key: crypto.randomUUID(), ...extra };
}

async function send(payload) {
  lastEvent = payload;
  el("replay").disabled = false;
  const ack = await api("/webhook/mock", "webhook", payload);
  show("ack", ack.status + " · " + ack.event_id);
  return ack.status === "accepted"
    ? "Đã lưu sự kiện. Worker xử lý và gửi phản hồi vào mock inbox."
    : "Webhook phản hồi " + ack.status + ".";
}

on("send", () => send({
  event_id: crypto.randomUUID(),
  group_id: el("group").value,
  sender_id: "demo-student",
  text: "/hoi " + required("question", "câu hỏi").replace(/^\/hoi\s+/i, ""),
  received_at: new Date().toISOString(),
}));
on("replay", () => send(lastEvent));

document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    el("question").value = presets[button.dataset.preset];
    if (button.dataset.preset === "n8n") el("group").value = "mock-ai-automation";
  });
});

on("refresh-ops", async () => {
  const [metrics, inbox] = await Promise.all([
    api("/internal/demo/metrics", "operator"),
    api("/internal/demo/deliveries?limit=15", "operator"),
  ]);
  show("answered", metrics.events.ANSWERED ?? 0);
  show("handoffs", metrics.events.HANDOFF ?? 0);
  show("delivered", metrics.mock_delivered ?? 0);
  const p95 = metrics.latency_ms.p95;
  show("latency", p95 == null ? "—" : Math.round(p95) + " ms");
  const cards = inbox.items.map((item) => {
    const card = node("article", "", "record");
    card.append(
      node("span", item.class_code + " · " + item.kind + " · #" + item.delivery_id, "badge"),
      node("p", item.body),
    );
    for (const citation of item.citations || []) {
      card.append(node("small", [citation.doc_id, citation.section_id, citation.locator].filter(Boolean).join(" · ")));
    }
    return card;
  });
  if (cards.length) el("inbox").replaceChildren(...cards);
  else replaceEmpty("inbox", "Chưa có phản hồi trong mock inbox.");
});

async function ticketList(cursor = 0) {
  const query = new URLSearchParams({ cursor, limit: 20 });
  if (el("ticket-filter").value) query.set("status", el("ticket-filter").value);
  const page = await api("/internal/teacher/tickets?" + query, "staff");
  nextCursor = page.next_cursor;
  el("next-tickets").hidden = nextCursor == null;
  const cards = page.items.map((ticket) => {
    const card = node("article", "", "record");
    const button = node("button", "#" + ticket.ticket_id + " · " + ticket.status + " · Lớp " + ticket.class_id);
    action(button, () => loadTicket(ticket.ticket_id));
    card.append(button, node("p", ticket.question_preview));
    return card;
  });
  if (cards.length) el("tickets").replaceChildren(...cards);
  else replaceEmpty("tickets", "Không có ticket phù hợp.");
}

function renderTicket(ticket) {
  const changed = activeTicket?.ticket_id !== ticket?.ticket_id;
  activeTicket = ticket;
  el("ticket-controls").hidden = !ticket;
  el("refresh-ticket").hidden = !ticket;
  show("ticket-title", ticket ? "Ticket #" + ticket.ticket_id : "Chi tiết ticket");
  show("ticket-summary", ticket
    ? [ticket.status, ticket.reason_code, ticket.question_preview].filter(Boolean).join("\n")
    : "Chọn một ticket từ hàng đợi.");
  show("reply-delivery", "");
  el("reply").value = ticket?.reply?.body || "";
  el("claim").disabled = ticket?.status !== "OPEN";
  const editable = ["ASSIGNED", "IN_REVIEW"].includes(ticket?.status);
  el("reply").disabled = !editable;
  el("draft").disabled = !editable;
  el("approve").disabled = !editable || ticket?.reply?.status !== "DRAFT";
  const resolved = ticket?.status === "RESOLVED";
  el("create-faq").disabled = !resolved;
  show("faq-ticket", resolved
    ? "Nguồn đề xuất · Ticket #" + ticket.ticket_id
    : "Chọn ticket đã RESOLVED ở tab giảng viên.");
  if (changed) {
    el("faq-question").value = "";
    el("faq-answer").value = "";
  }
}

async function loadTicket(id) {
  const ticket = await api("/internal/teacher/tickets/" + id, "staff");
  renderTicket(ticket);
  if (ticket.reply) {
    const delivery = await api("/internal/teacher/replies/" + ticket.reply.reply_id + "/delivery", "staff");
    show("reply-delivery", "Gửi mô phỏng · " + (delivery.delivery_status || "Chưa đưa vào hàng đợi"));
  }
}

async function ticketAction(path, extra) {
  const result = await api("/internal/teacher/tickets/" + activeTicket.ticket_id + "/" + path,
    "staff", command(activeTicket.version, extra));
  renderTicket(result);
  return "Ticket #" + result.ticket_id + " · " + result.status + ". Làm mới danh sách để cập nhật.";
}

on("refresh-tickets", () => ticketList());
on("next-tickets", () => ticketList(nextCursor));
on("refresh-ticket", () => loadTicket(activeTicket.ticket_id));
on("claim", () => ticketAction("claim"));
on("draft", () => ticketAction("reply-draft", { body: required("reply", "nội dung phản hồi") }));
el("reply").addEventListener("input", () => {
  el("approve").disabled = activeTicket?.reply?.status !== "DRAFT"
    || el("reply").value !== activeTicket.reply.body;
});
on("approve", () => ticketAction("approve-reply", {
  reply_revision: activeTicket.reply.revision,
  approved_body_hash: activeTicket.reply.body_hash,
}));

function renderFaq(faq) {
  activeFaq = faq;
  el("faq-controls").hidden = !faq;
  if (!faq) {
    show("faq-summary", "Chưa mở hồ sơ FAQ.");
    show("faq-content", "");
    return;
  }
  el("faq-id").value = faq.faq_id;
  show("faq-summary", "FAQ #" + faq.faq_id + " · " + faq.class_scope + " · " + faq.status
    + " · Bản " + faq.revision
    + (faq.publication_status ? " · Xuất bản: " + faq.publication_status : ""));
  show("faq-content", faq.question + "\n\n" + faq.answer);
  el("review-reason").value = "";
  el("review-field").hidden = faq.status !== "SUBMITTED";
  el("submit-faq").hidden = faq.status !== "DRAFT";
  el("review-faq").hidden = faq.status !== "SUBMITTED";
  el("reject-faq").hidden = faq.status !== "SUBMITTED";
  el("publish-faq").hidden = !["APPROVED", "FAILED"].includes(faq.status);
}

async function faqAction(path, extra) {
  const result = await api("/internal/faq/candidates/" + activeFaq.faq_id + "/" + path,
    "staff", command(activeFaq.revision, extra));
  renderFaq(result);
  return "FAQ #" + result.faq_id + " · " + result.status + ".";
}

on("create-faq", async () => {
  const result = await api("/internal/faq/candidates", "staff", command(activeTicket.version, {
    ticket_id: activeTicket.ticket_id,
    question: required("faq-question", "câu hỏi FAQ"),
    answer: required("faq-answer", "câu trả lời FAQ"),
  }));
  renderFaq(result);
  el("faq-question").value = "";
  el("faq-answer").value = "";
  return "Đã tạo nháp FAQ #" + result.faq_id + ".";
});
on("load-faq", async () => {
  const id = el("faq-id").valueAsNumber;
  if (!Number.isSafeInteger(id) || id < 1) throw new Error("Nhập FAQ ID hợp lệ.");
  renderFaq(await api("/internal/faq/candidates/" + id, "staff"));
});
on("submit-faq", () => faqAction("submit"));
on("publish-faq", () => faqAction("publish"));

function reviewFaq(decision) {
  return faqAction("review", {
    decision,
    candidate_body_hash: activeFaq.body_hash,
    reason: required("review-reason", "lý do đánh giá"),
  });
}

on("review-faq", () => reviewFaq("approve"));
on("reject-faq", () => reviewFaq("reject"));

el("staff-token").addEventListener("input", () => {
  renderTicket(null);
  renderFaq(null);
  el("review-reason").value = "";
  el("next-tickets").hidden = true;
  replaceEmpty("tickets", "Tải danh sách với staff token hiện tại.");
  notify("Đã đổi staff token. Tải lại hồ sơ trước khi thao tác.");
});
el("operator-token").addEventListener("input", () => {
  for (const id of ["answered", "handoffs", "delivered", "latency"]) show(id, "—");
  replaceEmpty("inbox", "Tải lại dữ liệu với operator token hiện tại.");
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    for (const view of document.querySelectorAll("[data-view]")) {
      view.setAttribute("aria-pressed", String(view === button));
      el(view.dataset.view).hidden = view !== button;
    }
  });
});
renderTicket(null);
