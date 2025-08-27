/* ========= util ========= */
const $ = (sel) => document.querySelector(sel);
const log = $("#log");
const phoneIpt = $("#phone");
const problemIpt = $("#problem");
const resinSel = $("#resin");
const printerSel = $("#printer");
const btnHistory = $("#btnHistory");
const btnSend = $("#btnSend");
const dot = $("#dot");
const statusSpan = $("#status");
const imagesInp = $("#images");
const previewBox = $("#preview");
const btnLegal = $("#btnLegal");
const dlgLegal = $("#legalModal");
const btnCloseLegal = $("#closeLegal");

let currentPhone = "";

/* ========= UI helpers ========= */
function normalizePhone(v){
  return (v || "").replace(/\D+/g,"").slice(0,15);
}
function setStatus(type, text){
  dot.classList.remove("ok","wait","err");
  if(type==="ok") dot.classList.add("ok");
  if(type==="wait") dot.classList.add("wait");
  if(type==="err") dot.classList.add("err");
  statusSpan.textContent = text;
}
function addMsg(role, content){
  const row = document.createElement("div");
  row.className = "msg " + (role==="user" ? "you" : "bot");
  const bub = document.createElement("div");
  bub.className = "bubble";
  bub.textContent = content;
  row.appendChild(bub);
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}
function clearChatUI(){
  log.innerHTML = `<div class="hint center">
    Nenhuma conversa ainda. Informe o telefone e carregue o histórico 👇
  </div>`;
}

/* ========= direitos autorais ========= */
btnLegal.addEventListener("click", ()=> dlgLegal.showModal());
btnCloseLegal.addEventListener("click", ()=> dlgLegal.close());

/* ========= preview de imagens ========= */
imagesInp.addEventListener("change", ()=>{
  previewBox.innerHTML = "";
  const files = [...imagesInp.files].slice(0,5);
  files.forEach(f=>{
    const url = URL.createObjectURL(f);
    const img = document.createElement("img");
    img.src = url; previewBox.appendChild(img);
  });
});

/* ========= telefone: limpar chat ao alterar ========= */
phoneIpt.addEventListener("input", ()=>{
  const clean = normalizePhone(phoneIpt.value);
  phoneIpt.value = clean;
  if(clean !== currentPhone){
    clearChatUI();
    setStatus("wait", "Aguardando…");
  }
});

/* ========= carregar histórico ========= */
async function loadHistory(){
  const phone = normalizePhone(phoneIpt.value);
  if(!phone){ alert("Digite o telefone (somente números)."); return; }
  setStatus("wait","Carregando histórico…");
  try{
    const res = await fetch(`/history?phone=${encodeURIComponent(phone)}`);
    const data = await res.json();
    log.innerHTML = "";
    if(!data.length){
      clearChatUI();
    }else{
      for(const m of data){
        addMsg(m.role, m.content);
      }
    }
    currentPhone = phone;
    setStatus("ok","Concluído");
  }catch(e){
    console.error(e);
    setStatus("err","Falha ao carregar histórico");
  }
}
btnHistory.addEventListener("click", loadHistory);

/* ========= enviar para IA ========= */
async function sendToAI(){
  const phone = normalizePhone(phoneIpt.value);
  if(!phone){ alert("Digite o telefone (somente números)."); return; }

  const problem = (problemIpt.value||"").trim();
  const resin = resinSel.value||"Não informar";
  const printer = printerSel.value||"Não informar";
  if(!problem && (!imagesInp.files || imagesInp.files.length===0)){
    alert("Descreva o problema ou envie pelo menos 1 imagem.");
    return;
  }

  // UI
  addMsg("user", problem || "[Mensagem com imagens]");
  setStatus("wait","Consultando a IA…");

  try{
    const form = new FormData();
    form.append("phone", phone);
    form.append("problem", problem);
    form.append("resin", resin);
    form.append("printer", printer);

    const files = [...(imagesInp.files||[])].slice(0,5);
    files.forEach(f => form.append("images", f));

    const res = await fetch("/chat", { method:"POST", body: form });
    const data = await res.json();

    if(data.error){
      setStatus("err","Erro na IA");
      addMsg("assistant", "Falha: " + data.error);
      return;
    }

    addMsg("assistant", data.reply);
    setStatus("ok","Concluído");
    currentPhone = phone; // trava o phone atual
  }catch(e){
    console.error(e);
    setStatus("err","Falha ao enviar");
    addMsg("assistant", "Falha ao enviar sua mensagem.");
  }
}
btnSend.addEventListener("click", sendToAI);

/* ========= estado inicial ========= */
clearChatUI();
setStatus("wait","Aguardando…");
