// 调试：控制台执行 window.DEBUG=1（或 DEBUG=1）后输出 SSE / 预览等 console 日志
// 主题与全页只读选区：见 theme-ui.js（经典页内联包首部已包含）
const CONVERSATION_STORAGE_KEY="codeWebAgent.activeConversationId";
const CONVERSATION_TABS_STORAGE_KEY="codeWebAgent.openConversationTabs";
function newConversationId(){return crypto.randomUUID?crypto.randomUUID():'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g,function(c){const r=Math.random()*16|0;return(c==='x'?r:(r&0x3|0x8)).toString(16);});}
function readCookieValue(name){const m=document.cookie.match(new RegExp("(?:^|;\\s*)"+name.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+"=([^;]+)"));return m?decodeURIComponent(m[1]):"";}
function clearCookie(name){document.cookie=name+"=; path=/; max-age=0; SameSite=Lax";}
function normalizeConversationId(id){id=String(id||"").trim();return /^[A-Za-z0-9._:-]{8,128}$/.test(id)?id:"";}
function getUrlConversationId(){try{const p=new URLSearchParams(window.location.search);return normalizeConversationId(p.get("conversation_id")||p.get("cid")||"");}catch(e){return "";}}
function getStoredConversationId(){try{return normalizeConversationId(sessionStorage.getItem(CONVERSATION_STORAGE_KEY)||"");}catch(e){return "";}}
function storeConversationId(id){try{sessionStorage.setItem(CONVERSATION_STORAGE_KEY,id);}catch(e){}}
function getStoredConversationLayout(){try{var raw=localStorage.getItem(CONVERSATION_TABS_STORAGE_KEY)||"";var o=raw?JSON.parse(raw):null;if(!o||typeof o!=="object")return null;var tabs=Array.isArray(o.tabs)?o.tabs:[];tabs=tabs.map(function(t){return {id:normalizeConversationId(t&&t.id),title:String(t&&t.title||"").slice(0,80)};}).filter(function(t){return !!t.id;});var active=normalizeConversationId(o.active_conversation_id||o.activeConversationId||"");return tabs.length?{active_conversation_id:active||tabs[0].id,tabs:tabs}:null;}catch(e){return null;}}
function storeConversationLayoutLocal(){try{localStorage.setItem(CONVERSATION_TABS_STORAGE_KEY,JSON.stringify({active_conversation_id:activeConversationId,tabs:conversationTabs.map(function(t){return {id:t.id,title:t.title||("会话 "+t.id.slice(0,8))};})}));}catch(e){}}
function getOrCreateConversationId(){let id=getUrlConversationId();if(id){storeConversationId(id);return id;}id=getStoredConversationId();if(id)return id;var layout=getStoredConversationLayout();if(layout&&layout.active_conversation_id){storeConversationId(layout.active_conversation_id);return layout.active_conversation_id;}id=normalizeConversationId(readCookieValue("sessionId"));if(id){storeConversationId(id);clearCookie("sessionId");return id;}id=newConversationId();storeConversationId(id);return id;}
function createDetachedHost(className){var d=document.createElement("div");d.className=className||"";return d;}
function buildTodoArea(){var n=document.createElement("div");n.className="todo-list-area hidden";n.style.display="";var hdr=document.createElement("div");hdr.className="todo-list-header";var ttl=document.createElement("span");ttl.className="todo-list-title";ttl.textContent="📋 Todo List";var cnt=document.createElement("span");cnt.className="todo-list-count";cnt.textContent="0/0";var ic=document.createElement("span");ic.className="todo-collapse-icon";ic.textContent="▼";hdr.appendChild(ttl);hdr.appendChild(cnt);hdr.appendChild(ic);hdr.addEventListener("click",function(e){if(e.target.closest&&e.target.closest(".todo-list-count"))return;n.classList.toggle("collapsed");});var bd=document.createElement("div");bd.className="todo-list-body";var sc=document.createElement("div");sc.className="todo-list-scroll";bd.appendChild(sc);n.appendChild(hdr);n.appendChild(bd);return n;}
function makeConversationTab(id){id=normalizeConversationId(id)||newConversationId();var pane=document.createElement("div");pane.className="chat-conversation-pane";var mh=createDetachedHost("msgs");var td=buildTodoArea();pane.appendChild(mh);pane.appendChild(td);return {id:id,title:"会话 "+id.slice(0,8),conversationPane:pane,msgsHost:mh,stepsHost:createDetachedHost("steps"),todoArea:td,selectedMode:"auto",selectedModel:"deepseek-v4-flash",abortController:null,activeRunId:"",stopRequested:false,stepSeq:0,lastLlm:null,llmStreamBuffer:{round:null,reqHtml:"",resHtml:"",consumed:false},pendingToolTags:[],anyToolThisTurn:false,pendingStepEls:[],streamAssistantEl:null,streamAssistantText:"",pendingDeltaSeparator:false,seenDispatchTitle:"",toolOpen:new Map(),lastAnalysisTail:"",chatLoadingEl:null,userConfirmCardHost:null,userConfirmBlocking:false,todoUnread:false,sessionTokenUsed:0,totalPromptTokens:0,totalCompletionTokens:0,totalCacheHitTokens:0,totalCacheMissTokens:0,pricingCacheKey:"",pricingRates:null,pricingFailed:false,pricingLoading:false,pricingInflight:false,pricingFetchGen:0,lastContextLayout:null};}
const initialConversationId=getOrCreateConversationId();
let _initialLayout=getStoredConversationLayout();
let conversationTabs=(_initialLayout&&_initialLayout.tabs.length?_initialLayout.tabs.map(function(t){var tab=makeConversationTab(t.id);tab.title=t.title||tab.title;return tab;}):[makeConversationTab(initialConversationId)]);
let activeConversationId=initialConversationId;
if(_initialLayout&&findConversationTab(_initialLayout.active_conversation_id))activeConversationId=_initialLayout.active_conversation_id;
try{var _fromImm=normalizeConversationId(sessionStorage.getItem(CONVERSATION_STORAGE_KEY)||"");if(_fromImm){ensureConversationTab(_fromImm);activeConversationId=_fromImm;storeConversationId(_fromImm);}}catch(_eI){}
function getActiveTab(){for(var i=0;i<conversationTabs.length;i++){if(conversationTabs[i].id===activeConversationId)return conversationTabs[i];}var t=makeConversationTab(activeConversationId||newConversationId());conversationTabs.push(t);return t;}
function findConversationTab(id){id=normalizeConversationId(id);for(var i=0;i<conversationTabs.length;i++){if(conversationTabs[i].id===id)return conversationTabs[i];}return null;}
function ensureConversationTab(id){id=normalizeConversationId(id)||newConversationId();var t=findConversationTab(id);if(!t){t=makeConversationTab(id);conversationTabs.push(t);if(typeof renderChatTabs==="function")renderChatTabs();}return t;}
function getActiveConversationId(){return activeConversationId;}
function setActiveConversationId(id,opts){id=normalizeConversationId(id)||newConversationId();var t=ensureConversationTab(id);activeConversationId=id;sessionId=id;conv=id;storeConversationId(id);t.id=id;t.title=t.title||("会话 "+id.slice(0,8));if(typeof cid!=="undefined"&&cid)cid.textContent=id.slice(0,8);if(!opts||opts.resetPricing!==false){pricingRates=null;pricingFailed=false;pricingCacheKey="";pricingLoading=false;pricingInflight=false;pricingFetchGen++;}if(typeof renderChatTabs==="function")renderChatTabs();if(typeof _updateUsageBottom==="function")_updateUsageBottom();}
let sessionId=activeConversationId;let conv=sessionId,stepSeq=0,lastLlm=null;let llmStreamBuffer={round:null,reqHtml:"",resHtml:"",consumed:false};let pendingToolTags=[];let anyToolThisTurn=false;let pendingStepEls=[];let streamAssistantEl=null,streamAssistantText="";let pendingDeltaSeparator=false;let seenDispatchTitle="";const SESSION_TOKEN_LIMIT=1000000;let sessionTokenUsed=0;
let totalPromptTokens=0,totalCompletionTokens=0,totalCacheHitTokens=0,totalCacheMissTokens=0;
let pricingCacheKey="";let pricingRates=null;let pricingFailed=false;let pricingLoading=false;let pricingInflight=false;let pricingFetchGen=0;
var ctxLayoutTooltipEl=null;var ctxLayoutTooltipInited=false;var ctxLayoutTipHideTimer=null;
let renderingContextVisible=true;
let toolOpen=new Map();let lastAnalysisTail="";
let chatLoadingEl=null;
var userConfirmCardHost=null;
var userConfirmBlocking=false;
var todoListArea=null,todoListBody=null,todoListCount=null;
function showChatLoading(){if(chatLoadingEl||!msgs)return;chatLoadingEl=document.createElement("div");chatLoadingEl.className="b a";chatLoadingEl.style.textAlign="center";chatLoadingEl.style.padding="16px 0";chatLoadingEl.innerHTML="<span class=\"chat-spinner\"></span> <span style=\"color:#888;font-size:12px\">正在思考中…</span>";msgs.appendChild(chatLoadingEl);scrollMsgsToBottom();}
function hideChatLoading(){if(chatLoadingEl){try{chatLoadingEl.remove();}catch(e){}chatLoadingEl=null;}}
function closeUserConfirmCardHost(){if(userConfirmCardHost){try{userConfirmCardHost.remove();}catch(e){}userConfirmCardHost=null;}userConfirmBlocking=false;}

/* ===== Todo-List ===== */
function initTodoListElements(){
if(!todoListArea)return;
todoListBody=todoListArea.querySelector(".todo-list-body");
todoListCount=todoListArea.querySelector(".todo-list-count");
}
function showTodoList(){initTodoListElements();if(todoListArea){todoListArea.classList.remove("hidden");}}
function hideTodoList(){initTodoListElements();if(todoListArea){todoListArea.classList.add("hidden");}}
function renderTodoListFromEvent(ev,ownerCid){
initTodoListElements();
if(todoListArea&&todoListArea.style.display==="none"){todoListArea.style.display="";}
var items=Array.isArray(ev.items)?ev.items:[];
if(!items.length){if(ev.close&&todoListArea){todoListArea.style.display="none";todoListArea.classList.add("hidden");}hideTodoList();var _ocDn=normalizeConversationId(ownerCid||"");if(_ocDn){var _tDn=findConversationTab(_ocDn);if(_tDn){_tDn.todoUnread=false;if(typeof syncTodoUnreadBadgeForConversation==="function")syncTodoUnreadBadgeForConversation(_ocDn);}}return;}
showTodoList();
var doneCount=0;
var html="";
for(var i=0;i<items.length;i++){
var it=items[i];
var done=!!it.done;
if(done)doneCount++;
var cbClass="todo-cb"+(done?" done":"");
var txtClass="todo-text"+(done?" done":"");
html+='<div class="todo-item"><span class="'+cbClass+'"></span><span class="'+txtClass+'">'+escapeHtml(String(it.text||""))+'</span></div>';
}
if(todoListBody){var sc=todoListBody.querySelector(".todo-list-scroll");if(sc)sc.innerHTML=html;else todoListBody.innerHTML=html;}
if(todoListCount)todoListCount.textContent=doneCount+"/"+items.length;
if(ev.all_done&&todoListArea){todoListArea.style.borderLeft="3px solid #3daf3f";if(!todoListArea.querySelector(".todo-close-btn")){var cb=document.createElement("button");cb.className="todo-close-btn";cb.textContent="✕";cb.title="关闭清单";cb.onclick=function(){todoListArea.style.display="none";};var hd=todoListArea.querySelector(".todo-list-header");if(hd)hd.appendChild(cb);}}
else if(todoListArea){todoListArea.style.borderLeft="3px solid #a68b4a";var oldBtn=todoListArea.querySelector(".todo-close-btn");if(oldBtn)oldBtn.remove();}
if(ev.collapsed&&todoListArea){todoListArea.classList.add("collapsed");}
else if(ev.collapsed===false&&todoListArea){todoListArea.classList.remove("collapsed");}
if(ev.close&&todoListArea){todoListArea.style.display="none";}
var _owUr=normalizeConversationId(ownerCid||"");if(_owUr){if(ev.close){var _txUr=findConversationTab(_owUr);if(_txUr){_txUr.todoUnread=false;if(typeof syncTodoUnreadBadgeForConversation==="function")syncTodoUnreadBadgeForConversation(_owUr);}}else if(items.length&&_owUr!==normalizeConversationId(activeConversationId)){var _tyUr=findConversationTab(_owUr);if(_tyUr){_tyUr.todoUnread=true;if(typeof syncTodoUnreadBadgeForConversation==="function")syncTodoUnreadBadgeForConversation(_owUr);}}}
}
function escapeHtml(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
async function drainChatSseFromResponse(r,streamCid){
if(window.DEBUG){try{console.log("[SSE] 开始读流",{streamCid:String(streamCid||""),ok:r.ok,status:r.status,hasBody:!!(r&&r.body)});}catch(e){}}
if(!r||!r.body){if(window.DEBUG)console.error("[SSE] 响应无 body，无法读取流",{ok:r&&r.ok,status:r&&r.status});return;}
const rd=r.body.getReader(),de=new TextDecoder();let buf="";
let endedAwaitingUserConfirm=false;
for(;;){
const x=await rd.read();if(x.done){
if(window.DEBUG){try{console.log("[SSE] 流结束 EOF",{streamCid:String(streamCid||""),bufRemain:buf.length});}catch(e){}}
const sseCloseCid=normalizeConversationId(streamCid||"");if(!sseCloseCid){if(window.DEBUG)console.error("[SSE] 流结束：stream 绑定缺少有效 conversation_id，已跳过收尾渲染",{streamCid:String(streamCid||"")});break;}withConversationContext(sseCloseCid,function(){
if(endedAwaitingUserConfirm){hideChatLoading();return;}
if(!anyToolThisTurn&&!streamAssistantEl&&!lastLlm){hideChatLoading();}else if(toolOpen.size>0||pendingToolTags.length>0){
add("a","⚠️ **系统检测到意外断开连接。** 可能原因：网络波动、服务器繁忙、调用工具次数达到上限。部分工具已运行完毕（见右侧步骤面板），你可重新发起对话尝试恢复，或将复杂任务拆分为多步逐步执行。");
abortPendingToolTags();resetTurnState();hideChatLoading();if(lastLlm){finishLlmTitle(false);lastLlm.tag.className="tag bad";lastLlm.tag.textContent="截断";lastLlm=null;}
}
});
break;}
buf+=de.decode(x.value,{stream:true});
let i;while((i=buf.indexOf("\n\n"))>=0){
const blk=buf.slice(0,i);buf=buf.slice(i+2);
for(const line of blk.split("\n")){
if(line.indexOf("data:")!==0)continue;
const raw=line.slice(5).trim();let ev;try{ev=JSON.parse(raw);}catch(_jsonErr){if(window.DEBUG){console.warn("[SSE recv] JSON 解析失败",{error:String(_jsonErr&&_jsonErr.message||_jsonErr),sample:String(raw||"").slice(0,400)});console.error("[SSE] JSON 解析失败，已跳过本行（不渲染）",{error:String(_jsonErr&&_jsonErr.message||_jsonErr),sample:String(raw||"").slice(0,240)});}continue;}
if(window.DEBUG){try{console.log("[SSE recv]",{streamCid:String(streamCid||""),type:ev&&ev.type,conversation_id:ev&&ev.conversation_id,ev:ev});}catch(e){}}
const packetCid=normalizeConversationId(ev.conversation_id);if(!packetCid){if(window.DEBUG){console.warn("[SSE recv] 已丢弃（conversation_id 缺失或非法）",{streamCid:String(streamCid||""),type:ev&&ev.type,conversation_id:ev&&ev.conversation_id,ev:ev});console.error("[SSE] 数据包缺少或非法 conversation_id，已跳过渲染（不写入任何会话）",{type:ev&&ev.type,conversation_id:ev&&ev.conversation_id});}continue;}withConversationContext(packetCid,function(_ctxTab){if(ev.type==="conversation"){ensureConversationTab(packetCid);if(ev.mode)_ctxTab.selectedMode=normalizeMode(ev.mode);if(ev.model)_ctxTab.selectedModel=String(ev.model||"");if(renderingContextVisible){if(ev.mode)applyMode(ev.mode);if(ev.model)applyModel(ev.model);void ensureModelPricing();}}
else if(ev.type==="run_started"){_ctxTab.activeRunId=String(ev.run_id||"");}
else if(ev.type==="mode_changed"&&ev.mode){_ctxTab.selectedMode=normalizeMode(ev.mode);if(renderingContextVisible)applyMode(normalizeMode(ev.mode));}
else if(ev.type==="dispatch_title")addDispatchTitle(ev.title||"");
else if(ev.type==="llm_round")addLlmRound(ev.round);
else if(ev.type==="llm_request")onLlmRequest(ev);
else if(ev.type==="llm_response")onLlmResponse(ev);
else if(ev.type==="llm_done"){flushPendingToolTags();if(lastLlm){finishLlmTitle(true);lastLlm.tag.className="tag ok";lastLlm.tag.textContent="Done";}}
else if(ev.type==="usage"){const u=ev.usage||{};const inTok=Number(u.prompt_tokens??0)||0;const outTok=Number(u.completion_tokens??0)||0;const hitTok=Number(u.prompt_cache_hit_tokens??0)||0;const missTok=Number(u.prompt_cache_miss_tokens??0)||0;sessionTokenUsed+=Math.max(0,inTok+outTok);totalPromptTokens+=inTok;totalCompletionTokens+=outTok;totalCacheHitTokens+=hitTok;totalCacheMissTokens+=missTok;void ensureModelPricing();_updateUsageBottom();void persistUsageAccumulator();}
else if(ev.type==="context_layout"){_ctxTab.lastContextLayout=ev;if(renderingContextVisible&&packetCid===normalizeConversationId(activeConversationId))_updateUsageBottom();}
else if(ev.type==="tool_start")onToolStart(ev);
else if(ev.type==="tool_progress")onToolProgress(ev);
else if(ev.type==="tool_end"){onToolEnd(ev);if(ev.todo_list&&ev.todo_list_data){var _td=ev.todo_list_data;renderTodoListFromEvent({items:_td.items||[],all_done:Array.isArray(_td.items)&&_td.items.every(function(it){return !!it.done;}),collapsed:!!_td.collapsed,close:!!_td.close},packetCid);scrollToBottomAfterLayout(msgs,true);}}
else if(ev.type==="open_session"){var sid=normalizeConversationId(ev.session_id);if(sid){ensureConversationTab(sid);var _ot=findConversationTab(sid);if(_ot)_ot.title=ev.name||("会话 "+sid.slice(0,8));}}
else if(ev.type==="tool_preview_update"){var tid2=String(ev.tool_call_id||"").trim();if(tid2){var card=findStepCardForToolCall(tid2);if(card){var pb2=card.querySelector("pre.tool-res");if(pb2){try{var _pj2=JSON.parse(ev.preview||"{}");pb2.textContent=JSON.stringify(_pj2,null,2);}catch(e2){pb2.textContent=String(ev.preview||"");}var lb2=pb2.previousElementSibling;if(lb2&&lb2.classList&&lb2.classList.contains("lbl"))lb2.style.display="block";pb2.style.display="block";}var tag2=card.querySelector(".ch .tag");if(tag2){try{var _pj3=JSON.parse(ev.preview||"{}");if(_pj3&&_pj3.ok){tag2.textContent="Done";tag2.className="tag ok";}else{tag2.textContent="Fail";tag2.className="tag bad";}}catch(e3){}}}}}
else if(ev.type==="audio"){playAudio(packetCid||ev.conversation_id,ev.audio,ev.voice||"",ev._dbg||"");}
else if(ev.type==="assistant_delta"){if(anyToolThisTurn){flushPendingSteps();}appendAssistantDelta(ev.delta||"");}
else if(ev.type==="reasoning_delta")appendReasoningDelta(ev);
else if(ev.type==="reasoning_sync")applyReasoningSync(ev);
else if(ev.type==="assistant_markdown"){
if(anyToolThisTurn){flushPendingSteps();}
(function(){
var md=ev.markdown;if(typeof md!=="string"||!md.trim())return;
var e=ensureAssistantStreamBubble();if(!e)return;
if(streamAssistantText&&!streamAssistantText.endsWith("\n"))streamAssistantText+="\n";
streamAssistantText+=md.trim()+"\n";
e.innerHTML=renderMarkdown(streamAssistantText);
scrollMsgsToBottom();
})();
}
else if(ev.type==="assistant"){if(anyToolThisTurn){flushPendingSteps();}if(!finalizeAssistantStream(ev.content||"")){add("a",ev.content||"");}}
else if(ev.type==="done"){promoteReasoningToChatIfNeeded();finalizeAssistantStream("");void refreshConversationTitle(packetCid);}
else if(ev.type==="stopped"){markCurrentTurnStoped();resetTurnState();hideChatLoading();if(ev.message)add("a",ev.message);}
else if(ev.type==="paused_for_user_confirm"){hideChatLoading();endedAwaitingUserConfirm=true;}
else if(ev.type==="todo_list"){renderTodoListFromEvent(ev,packetCid);scrollToBottomAfterLayout(msgs,true);}
else if(ev.type==="error"){hideChatLoading();abortPendingToolTags();if(lastLlm){finishLlmTitle(false);lastLlm.tag.className="tag bad";lastLlm.tag.textContent="Fail";lastLlm=null;}
if(!anyToolThisTurn){discardPendingSteps();}else{pendingStepEls=[];}
add("a","错误: "+JSON.stringify(ev.detail||ev));}
});
_routeEventBySource(ev);
}
}
}
}
function handleGlobalSseEvent(ev){
if(!ev||ev.type==="heartbeat"||ev.type==="global_sse_ready")return;
const packetCid=normalizeConversationId(ev.conversation_id);if(!packetCid)return;
withConversationContext(packetCid,function(_ctxTab){
if(ev.type==="conversation"){ensureConversationTab(packetCid);if(ev.mode)_ctxTab.selectedMode=normalizeMode(ev.mode);if(ev.model)_ctxTab.selectedModel=String(ev.model||"");if(renderingContextVisible){if(ev.mode)applyMode(ev.mode);if(ev.model)applyModel(ev.model);void ensureModelPricing();}}
else if(ev.type==="run_started"){_ctxTab.activeRunId=String(ev.run_id||"");_ctxTab.abortController=_ctxTab.abortController||{global:true};showChatLoading();}
else if(ev.type==="mode_changed"&&ev.mode){_ctxTab.selectedMode=normalizeMode(ev.mode);if(renderingContextVisible)applyMode(normalizeMode(ev.mode));}
else if(ev.type==="dispatch_title")addDispatchTitle(ev.title||"");
else if(ev.type==="llm_round")addLlmRound(ev.round);
else if(ev.type==="llm_request")onLlmRequest(ev);
else if(ev.type==="llm_response")onLlmResponse(ev);
else if(ev.type==="llm_done"){flushPendingToolTags();if(lastLlm){finishLlmTitle(true);lastLlm.tag.className="tag ok";lastLlm.tag.textContent="Done";}}
else if(ev.type==="usage"){const u=ev.usage||{};const inTok=Number(u.prompt_tokens??0)||0;const outTok=Number(u.completion_tokens??0)||0;const hitTok=Number(u.prompt_cache_hit_tokens??0)||0;const missTok=Number(u.prompt_cache_miss_tokens??0)||0;sessionTokenUsed+=Math.max(0,inTok+outTok);totalPromptTokens+=inTok;totalCompletionTokens+=outTok;totalCacheHitTokens+=hitTok;totalCacheMissTokens+=missTok;void ensureModelPricing();_updateUsageBottom();void persistUsageAccumulator();}
else if(ev.type==="context_layout"){_ctxTab.lastContextLayout=ev;if(renderingContextVisible&&packetCid===normalizeConversationId(activeConversationId))_updateUsageBottom();}
else if(ev.type==="tool_start")onToolStart(ev);
else if(ev.type==="tool_progress")onToolProgress(ev);
else if(ev.type==="tool_end"){onToolEnd(ev);if(ev.todo_list&&ev.todo_list_data){var _td=ev.todo_list_data;renderTodoListFromEvent({items:_td.items||[],all_done:Array.isArray(_td.items)&&_td.items.every(function(it){return !!it.done;}),collapsed:!!_td.collapsed,close:!!_td.close},packetCid);scrollToBottomAfterLayout(msgs,true);}}
else if(ev.type==="open_session"){var sid=normalizeConversationId(ev.session_id);if(sid){ensureConversationTab(sid);var _ot=findConversationTab(sid);if(_ot)_ot.title=ev.name||("会话 "+sid.slice(0,8));}}
else if(ev.type==="tool_preview_update"){var tid2=String(ev.tool_call_id||"").trim();if(tid2){var card=findStepCardForToolCall(tid2);if(card){var pb2=card.querySelector("pre.tool-res");if(pb2){try{var _pj2=JSON.parse(ev.preview||"{}");pb2.textContent=JSON.stringify(_pj2,null,2);}catch(e2){pb2.textContent=String(ev.preview||"");}var lb2=pb2.previousElementSibling;if(lb2&&lb2.classList&&lb2.classList.contains("lbl"))lb2.style.display="block";pb2.style.display="block";}}}}
else if(ev.type==="assistant_delta"){if(anyToolThisTurn){flushPendingSteps();}appendAssistantDelta(ev.delta||"");}
else if(ev.type==="reasoning_delta")appendReasoningDelta(ev);
else if(ev.type==="reasoning_sync")applyReasoningSync(ev);
else if(ev.type==="assistant_markdown"){if(anyToolThisTurn){flushPendingSteps();}var md=ev.markdown;if(typeof md==="string"&&md.trim()){var e=ensureAssistantStreamBubble();if(e){if(streamAssistantText&&!streamAssistantText.endsWith("\n"))streamAssistantText+="\n";streamAssistantText+=md.trim()+"\n";e.innerHTML=renderMarkdown(streamAssistantText);scrollMsgsToBottom();}}}
else if(ev.type==="assistant"){if(anyToolThisTurn){flushPendingSteps();}if(!finalizeAssistantStream(ev.content||"")){add("a",ev.content||"");}}
else if(ev.type==="peer_message"){addPeerMessage(ev.content||"",ev.sender_name||"",ev.sender||"");}
else if(ev.type==="inbox_queued"){add("a","已收到来自 "+(ev.from_name||ev.from||"其他 Agent")+" 的排队消息。");}
else if(ev.type==="audio"){playAudio(packetCid||ev.conversation_id,ev.audio,ev.voice||"",ev._dbg||"");}
else if(ev.type==="done"){promoteReasoningToChatIfNeeded();finalizeAssistantStream("");_ctxTab.abortController=null;_ctxTab.activeRunId="";hideChatLoading();updateTaskControls();if(typeof renderChatTabs==="function")renderChatTabs();void refreshConversationTitle(packetCid);}
else if(ev.type==="stopped"){markCurrentTurnStoped();resetTurnState();hideChatLoading();if(ev.message)add("a",ev.message);_ctxTab.abortController=null;_ctxTab.activeRunId="";updateTaskControls();if(typeof renderChatTabs==="function")renderChatTabs();}
else if(ev.type==="paused_for_user_confirm"){hideChatLoading();}
else if(ev.type==="todo_list"){renderTodoListFromEvent(ev,packetCid);scrollToBottomAfterLayout(msgs,true);}
else if(ev.type==="error"){hideChatLoading();abortPendingToolTags();if(lastLlm){finishLlmTitle(false);lastLlm.tag.className="tag bad";lastLlm.tag.textContent="Fail";lastLlm=null;}if(!anyToolThisTurn){discardPendingSteps();}else{pendingStepEls=[];}add("a","错误: "+JSON.stringify(ev.detail||ev));_ctxTab.abortController=null;_ctxTab.activeRunId="";updateTaskControls();if(typeof renderChatTabs==="function")renderChatTabs();}
});
_routeEventBySource(ev);
}
function startGlobalSse(){
if(window.__codeWebAgentGlobalSse)return;
try{
var es=new EventSource("/api/events/stream");
window.__codeWebAgentGlobalSse=es;
es.onmessage=function(e){if(!e||!e.data)return;var ev;try{ev=JSON.parse(e.data);}catch(_err){return;}handleGlobalSseEvent(ev);};
es.onerror=function(){};
}catch(_e){}
}
function openUserConfirmModalFromToolEnd(ev){
if(userConfirmCardHost||!ev.user_confirm_required)return;
userConfirmBlocking=true;
if(goBtn)goBtn.disabled=true;
hideChatLoading();
var opts=Array.isArray(ev.user_confirm_options)?ev.user_confirm_options:[];
var title=String(ev.user_confirm_title||"请确认");
var multi=!!ev.user_confirm_multi;
var tailIdx=opts.length;
var wrap=document.createElement("div");wrap.className="b a user-confirm-card-outer";
var card=document.createElement("div");card.className="chat-diff-card user-confirm-card";
var cap=document.createElement("div");cap.className="chat-diff-cap user-confirm-cap";
var capLine=document.createElement("div");capLine.className="user-confirm-cap-line";
var h=document.createElement("span");h.className="uc-title";h.textContent=title;
var badge=document.createElement("span");badge.className="user-confirm-badge";
badge.textContent=multi?"待确认·多选":"待确认·单选";
capLine.appendChild(h);capLine.appendChild(badge);cap.appendChild(capLine);card.appendChild(cap);
var body=document.createElement("div");body.className="user-confirm-body";
var btns=document.createElement("div");btns.className="user-confirm-opts";
var pickSingle=-1;
var pickMulti={};
var rows=[];
var customInputEl=null;
function syncRows(){
for(var si=0;si<rows.length;si++){
var R=rows[si];
var on=multi?!!pickMulti[R.idx]:pickSingle===R.idx;
R.icon.classList.toggle("uc-on",on);
R.row.classList.toggle("uc-row-picked",on);
}}
function toggleIdx(idx){
if(multi){if(pickMulti[idx]){delete pickMulti[idx];}else{pickMulti[idx]=1;}syncRows();}
else{pickSingle=(pickSingle===idx)?-1:idx;syncRows();}}
function buildFinal(){
var pref="自定义说明：";
if(multi){
var keys=Object.keys(pickMulti).map(function(x){return parseInt(x,10);}).filter(function(x){return !isNaN(x)&&x>=0&&x<=tailIdx;});
keys.sort(function(a,b){return a-b;});
if(!keys.length)return "";
var parts=[];
for(var ki=0;ki<keys.length;ki++){
var j=keys[ki];
if(j===tailIdx){var ex2=customInputEl?String(customInputEl.value||"").trim():"";if(ex2)parts.push(pref+ex2);}
else{parts.push(String(opts[j]||""));}
}
return parts.join("\n");
}
if(pickSingle<0||pickSingle>tailIdx)return "";
if(pickSingle===tailIdx){var ex=customInputEl?String(customInputEl.value||"").trim():"";return ex?pref+ex:"";}
return String(opts[pickSingle]||"");
}
function addRow(idx,label,isTail){
var row=document.createElement("div");
row.className="uc-opt-row"+(multi?" uc-multi":" uc-single")+(isTail?" uc-has-custom":"");
var icon=document.createElement("span");icon.className=multi?"uc-check":"uc-radio";icon.setAttribute("aria-hidden","true");
row.appendChild(icon);
if(isTail){
var inp=document.createElement("input");inp.type="text";inp.className="uc-opt-input";inp.placeholder="自定义说明";inp.autocomplete="off";customInputEl=inp;
inp.addEventListener("focus",function(){if(multi){pickMulti[idx]=1;}else{pickSingle=idx;}syncRows();});
row.appendChild(inp);
}else{
var lab=document.createElement("span");lab.className="uc-opt-label";lab.textContent=label;
row.appendChild(lab);
}
row.onclick=function(e){if(e.target&&e.target.closest&&e.target.closest(".uc-opt-input"))return;toggleIdx(idx);};
btns.appendChild(row);rows.push({row:row,icon:icon,idx:idx});
}
for(var oi=0;oi<opts.length;oi++){addRow(oi,String(opts[oi]),false);}
addRow(tailIdx,"",true);
syncRows();
body.appendChild(btns);
var act=document.createElement("div");act.className="user-confirm-actions";
var cancel=document.createElement("button");cancel.type="button";cancel.className="mode-plus";cancel.textContent="取消";
var ok=document.createElement("button");ok.type="button";ok.className="mode-plus";ok.textContent="确认";
act.appendChild(cancel);act.appendChild(ok);body.appendChild(act);
card.appendChild(body);wrap.appendChild(card);msgs.appendChild(wrap);userConfirmCardHost=wrap;scrollMsgsToBottom();
function cleanup(){closeUserConfirmCardHost();updateTaskControls();}
var submitUserConfirm=async function(finalTxt){
var confirmCid=getActiveConversationId();cleanup();showChatLoading();var gb=goBtn;if(gb)gb.disabled=true;var tab=findConversationTab(confirmCid);if(tab){tab.abortController={global:true};tab.stopRequested=false;}updateTaskControls();
try{
var r=await fetch("/api/chat/user-confirm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:confirmCid,confirm:finalTxt,mode:selectedMode,model:selectedModel})});
if(!r.ok){var _bc="";try{_bc=await r.text();}catch(e){}if(window.DEBUG)console.error("[code-web-agent] /api/chat/user-confirm 非 OK",{status:r.status,bodyHead:String(_bc||"").slice(0,500)});withConversationContext(confirmCid,function(){hideChatLoading();add("a","确认请求 HTTP "+r.status);});if(tab){tab.abortController=null;tab.activeRunId="";}return;}
var _jr=await r.json();if(tab&&_jr&&_jr.run_id)tab.activeRunId=String(_jr.run_id||"");
}catch(err){if(err&&err.name==="AbortError"){return;}withConversationContext(confirmCid,function(){hideChatLoading();add("a","确认请求失败: "+(err&&err.message?err.message:String(err)));});}
finally{updateTaskControls();}
};
cancel.onclick=function(){void submitUserConfirm("");};
ok.onclick=function(){void submitUserConfirm(buildFinal());};
}
var conversationMount=document.getElementById("chatConversationMount");
if(!conversationMount){
var sec=document.querySelector("section.chat");
if(sec){
conversationMount=document.createElement("div");
conversationMount.id="chatConversationMount";
conversationMount.className="chat-conversation-mount";
var rowBlk=sec.querySelector(".row");
var tr=sec.querySelector(".chat-title-row");
if(tr&&rowBlk)sec.insertBefore(conversationMount,rowBlk);
else if(tr&&tr.nextSibling)sec.insertBefore(conversationMount,tr.nextSibling);
else sec.appendChild(conversationMount);
}
}
conversationMount&&conversationMount.classList.add("chat-conversation-mount");
const stepsRoot=document.getElementById("steps");let msgs=null,msgsRoot=null;let steps=stepsRoot;cid=document.getElementById("cid"),ta=document.getElementById("t"),slashPop=document.getElementById("slashPop"),sideTabbar=document.getElementById("sideTabbar"),paneSteps=document.getElementById("paneSteps"),paneKb=document.getElementById("paneKb"),hdrModel=document.getElementById("hdrModel"),modelSubmenu=document.getElementById("modelSubmenu"),modelSubWrap=document.getElementById("modelSubWrap"),chatTabs=document.getElementById("chatTabs");var _tBoot=getActiveTab();if(conversationMount&&_tBoot&&_tBoot.conversationPane){conversationMount.appendChild(_tBoot.conversationPane);msgsRoot=_tBoot.msgsHost;msgs=msgsRoot;todoListArea=_tBoot.todoArea;todoListBody=todoListArea?todoListArea.querySelector(".todo-list-body"):null;todoListCount=todoListArea?todoListArea.querySelector(".todo-list-count"):null;}if(cid)cid.textContent=getActiveConversationId().slice(0,8);
function moveChildrenToArray(el){var arr=[];if(!el)return arr;while(el.firstChild){arr.push(el.firstChild);el.removeChild(el.firstChild);}return arr;}
function restoreChildrenFromArray(el,arr){if(!el)return;el.innerHTML="";arr=Array.isArray(arr)?arr:[];for(var i=0;i<arr.length;i++){el.appendChild(arr[i]);}}
function isConversationBusy(){var t=getActiveTab();return !!(userConfirmBlocking||toolOpen.size||pendingToolTags.length||(t&&t.abortController));}
function isTabBusy(t){return !!(t&&(t.abortController||t.userConfirmBlocking||(t.toolOpen&&t.toolOpen.size)||((t.pendingToolTags||[]).length)));}
function saveRuntimeStateToTab(t){t.selectedMode=selectedMode;t.selectedModel=selectedModel;t.stepSeq=stepSeq;t.lastLlm=lastLlm;t.llmStreamBuffer=llmStreamBuffer;t.pendingToolTags=pendingToolTags;t.anyToolThisTurn=anyToolThisTurn;t.pendingStepEls=pendingStepEls;t.streamAssistantEl=streamAssistantEl;t.streamAssistantText=streamAssistantText;t.pendingDeltaSeparator=pendingDeltaSeparator;t.seenDispatchTitle=seenDispatchTitle;t.toolOpen=toolOpen;t.lastAnalysisTail=lastAnalysisTail;t.chatLoadingEl=chatLoadingEl;t.userConfirmCardHost=userConfirmCardHost;t.userConfirmBlocking=userConfirmBlocking;t.sessionTokenUsed=sessionTokenUsed;t.totalPromptTokens=totalPromptTokens;t.totalCompletionTokens=totalCompletionTokens;t.totalCacheHitTokens=totalCacheHitTokens;t.totalCacheMissTokens=totalCacheMissTokens;t.pricingCacheKey=pricingCacheKey;t.pricingRates=pricingRates;t.pricingFailed=pricingFailed;t.pricingLoading=pricingLoading;t.pricingInflight=pricingInflight;t.pricingFetchGen=pricingFetchGen;}
function loadRuntimeStateFromTab(t){selectedMode=t.selectedMode||selectedMode||"auto";selectedModel=t.selectedModel||selectedModel||"deepseek-v4-flash";stepSeq=Number(t.stepSeq||0);lastLlm=t.lastLlm||null;llmStreamBuffer=t.llmStreamBuffer||{round:null,reqHtml:"",resHtml:"",consumed:false};pendingToolTags=t.pendingToolTags||[];anyToolThisTurn=!!t.anyToolThisTurn;pendingStepEls=t.pendingStepEls||[];streamAssistantEl=t.streamAssistantEl||null;streamAssistantText=String(t.streamAssistantText||"");pendingDeltaSeparator=!!t.pendingDeltaSeparator;seenDispatchTitle=String(t.seenDispatchTitle||"");toolOpen=t.toolOpen instanceof Map?t.toolOpen:new Map();lastAnalysisTail=String(t.lastAnalysisTail||"");chatLoadingEl=t.chatLoadingEl||null;userConfirmCardHost=t.userConfirmCardHost||null;userConfirmBlocking=!!t.userConfirmBlocking;sessionTokenUsed=Number(t.sessionTokenUsed||0);totalPromptTokens=Number(t.totalPromptTokens||0);totalCompletionTokens=Number(t.totalCompletionTokens||0);totalCacheHitTokens=Number(t.totalCacheHitTokens||0);totalCacheMissTokens=Number(t.totalCacheMissTokens||0);pricingCacheKey=String(t.pricingCacheKey||"");pricingRates=t.pricingRates||null;pricingFailed=!!t.pricingFailed;pricingLoading=!!t.pricingLoading;pricingInflight=!!t.pricingInflight;pricingFetchGen=Number(t.pricingFetchGen||0);}
function moveVisibleToTab(t){while(stepsRoot.firstChild)t.stepsHost.appendChild(stepsRoot.firstChild);if(t.conversationPane&&conversationMount&&t.conversationPane.parentNode===conversationMount){conversationMount.removeChild(t.conversationPane);}saveRuntimeStateToTab(t);}
function moveTabToVisible(t){while(t.stepsHost.firstChild)stepsRoot.appendChild(t.stepsHost.firstChild);if(conversationMount&&t.conversationPane){while(conversationMount.firstChild){conversationMount.removeChild(conversationMount.firstChild);}conversationMount.appendChild(t.conversationPane);}msgsRoot=t.msgsHost;msgs=msgsRoot;todoListArea=t.todoArea;todoListBody=todoListArea?todoListArea.querySelector(".todo-list-body"):null;todoListCount=todoListArea?todoListArea.querySelector(".todo-list-count"):null;steps=stepsRoot;loadRuntimeStateFromTab(t);}
function saveActiveConversationView(){moveVisibleToTab(getActiveTab());}
function restoreConversationView(t){moveTabToVisible(t);if(cid)cid.textContent=t.id.slice(0,8);applyMode(selectedMode);applyModel(selectedModel);if(typeof updateTaskControls==="function")updateTaskControls();_updateUsageBottom();scrollToBottom(stepsRoot,true);renderKbPanel();if(t&&t.historyLoaded){scrollToBottomAfterLayout(msgs,true);}}
function withConversationContext(conversationId,fn){var target=ensureConversationTab(conversationId);var prevTab=getActiveTab(),prevMsgs=msgs,prevSteps=steps,prevTodo=todoListArea,prevTodoBody=todoListBody,prevTodoCount=todoListCount,prevRenderingVisible=renderingContextVisible;saveRuntimeStateToTab(prevTab);renderingContextVisible=target.id===activeConversationId;if(target.id===activeConversationId){msgsRoot=target.msgsHost;msgs=msgsRoot;steps=stepsRoot;todoListArea=target.todoArea;todoListBody=todoListArea?todoListArea.querySelector(".todo-list-body"):null;todoListCount=todoListArea?todoListArea.querySelector(".todo-list-count"):null;}else{msgs=target.msgsHost;steps=target.stepsHost;todoListArea=target.todoArea;todoListBody=todoListArea?todoListArea.querySelector(".todo-list-body"):null;todoListCount=todoListArea?todoListArea.querySelector(".todo-list-count"):null;}loadRuntimeStateFromTab(target);try{return fn(target);}finally{saveRuntimeStateToTab(target);renderingContextVisible=prevRenderingVisible;msgs=prevMsgs;steps=prevSteps;todoListArea=prevTodo;todoListBody=prevTodoBody;todoListCount=prevTodoCount;loadRuntimeStateFromTab(prevTab);if(cid)cid.textContent=(prevTab?prevTab.id:activeConversationId).slice(0,8);if(typeof _updateUsageBottom==="function")_updateUsageBottom();}}
var _layoutPersistTimer=null;
function conversationLayoutPayload(){var tabs=conversationTabs.slice(-8).map(function(t){return {id:t.id,title:t.title||("会话 "+t.id.slice(0,8))};});if(activeConversationId&&!tabs.some(function(t){return t.id===activeConversationId;})){tabs=tabs.length>=8?tabs.slice(1):tabs;tabs.push({id:activeConversationId,title:(findConversationTab(activeConversationId)||{}).title||("会话 "+activeConversationId.slice(0,8))});}return {active_conversation_id:activeConversationId,tabs:tabs};}
function persistConversationLayout(){storeConversationLayoutLocal();if(_layoutPersistTimer)clearTimeout(_layoutPersistTimer);_layoutPersistTimer=setTimeout(function(){try{fetch("/api/chat/ui-state",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(conversationLayoutPayload())}).catch(function(){});}catch(e){}},250);}
function openConversationFromMenu(id,title){id=normalizeConversationId(id);if(!id)return;saveActiveConversationView();var t=ensureConversationTab(id);if(title)t.title=String(title).slice(0,80);t.todoUnread=false;activeConversationId=t.id;sessionId=t.id;conv=t.id;storeConversationId(t.id);restoreConversationView(t);renderChatTabs();persistConversationLayout();loadConversationHistory(t);if(chatSessionMenu)chatSessionMenu.classList.add("hidden");}
function renderSessionMenu(items){if(!chatSessionMenu)return;items=Array.isArray(items)?items:[];chatSessionMenu.innerHTML="";var newBtn=document.createElement("button");newBtn.type="button";newBtn.className="chat-session-row chat-session-new";newBtn.textContent="新会话 +";newBtn.onclick=function(){createConversationTab();chatSessionMenu.classList.add("hidden");};chatSessionMenu.appendChild(newBtn);if(!items.length){var emp=document.createElement("div");emp.className="chat-session-empty";emp.textContent="暂无历史会话";chatSessionMenu.appendChild(emp);return;}var lastGroup=null;items.forEach(function(s){var id=normalizeConversationId(s&&s.id);if(!id)return;var group=String(s.date_group||"");if(group&&group!==lastGroup){lastGroup=group;var gh=document.createElement("div");gh.className="chat-session-group";gh.textContent=group;chatSessionMenu.appendChild(gh);}else if(!group&&lastGroup!==null){lastGroup=null;}var opened=!!findConversationTab(id);var row=document.createElement("button");row.type="button";row.className="chat-session-row";row.title=id;var title=document.createElement("span");title.className="chat-session-title";title.textContent=String(s.title||("会话 "+id.slice(0,8)));row.appendChild(title);if(opened){var badge=document.createElement("span");badge.className="chat-session-badge";badge.textContent="已打开";row.appendChild(badge);}row.onclick=function(){openConversationFromMenu(id,title.textContent);};chatSessionMenu.appendChild(row);});}
async function toggleSessionMenu(){if(!chatSessionMenu)return;if(!chatSessionMenu.classList.contains("hidden")){chatSessionMenu.classList.add("hidden");return;}chatSessionMenu.classList.remove("hidden");chatSessionMenu.innerHTML='<div class="chat-session-empty">加载中…</div>';try{var r=await fetch("/api/chat/sessions");var j=await r.json();renderSessionMenu(j&&j.sessions||[]);}catch(e){chatSessionMenu.innerHTML='<div class="chat-session-empty">会话列表加载失败</div>';}}
function isDefaultConversationTitle(t){var s=String(t&&t.title||"");return !s||/^会话\s+[A-Za-z0-9._:-]{8}$/.test(s)||s==="生成标题中…"||s==="新会话";}
async function refreshConversationTitle(id){var t=findConversationTab(id);if(!t||!isDefaultConversationTitle(t))return;t.title="生成标题中…";renderChatTabs();persistConversationLayout();try{var r=await fetch("/api/chat/title",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:id})});if(!r.ok)return;var j=await r.json();var title=String(j&&j.title||"").trim();if(title&&title!=="新会话"){var tt=findConversationTab(id);if(tt){tt.title=title.slice(0,18);renderChatTabs();persistConversationLayout();}}else if(title==="新会话"){var t2=findConversationTab(id);if(t2&&t2.title==="生成标题中…"){t2.title="会话 "+id.slice(0,8);renderChatTabs();persistConversationLayout();}}}catch(e){}}
function renderHistoryItems(items){
items=Array.isArray(items)?items:[];
var cid=getActiveConversationId();
for(var i=0;i<items.length;i++){
var it=items[i]||{};
if(it.role==="user"){
var bubble=null;
if(it._sender&&it._sender!=="boss")addPeerMessage(String(it.content||""),String(it._sender_name||""),String(it._sender||""));
else bubble=add("u",String(it.content||""));
var atts=Array.isArray(it.attachments)?it.attachments:[];
if(atts.length&&window.CWA&&CWA.buildMsgAttachStrip&&bubble){
bubble.appendChild(CWA.buildMsgAttachStrip(cid,atts,null));
}else if(it.had_images&&bubble){
if(window.CWA&&CWA.appendHadImagesLostTip)CWA.appendHadImagesLostTip(bubble);
else appendHadImagesLostTip(bubble);
}
}else if(it.role==="assistant"){
var ac=String(it.content||"").trim();
if(ac)add("a",ac);
}
}
}
function appendHadImagesLostTip(mount){if(!mount)return;var tip=document.createElement("div");tip.className="attach-lost-tip";tip.textContent="图片预览已失效；若需再看请重新粘贴后发送。";mount.appendChild(tip);}
function applyContextLayoutFromHistory(t,j){if(!t||!j)return;if(j.context_layout)t.lastContextLayout=j.context_layout;if(j.context_layout&&normalizeConversationId(t.id)===normalizeConversationId(activeConversationId))_updateUsageBottom();}
async function refreshConversationContextLayout(t){if(!t||!normalizeConversationId(t.id)||t.abortController)return;try{var r=await fetch("/api/chat/history?"+new URLSearchParams({conversation_id:t.id}));if(!r.ok)return;var j=await r.json();if(!j||j.ok!==true)return;applyContextLayoutFromHistory(t,j);}catch(e){}}
function scrollToBottomAfterLayout(el,force){if(!el)return;requestAnimationFrame(function(){requestAnimationFrame(function(){scrollToBottom(el,force);});});}
async function loadConversationHistory(t){if(!t||t.abortController)return;if(t.historyLoaded){void refreshConversationContextLayout(t);return;}t.historyLoaded=true;try{var r=await fetch("/api/chat/history?"+new URLSearchParams({conversation_id:t.id}));if(!r.ok)return;var j=await r.json();if(!j||j.ok!==true)return;var items=j.items||[];applyContextLayoutFromHistory(t,j);withConversationContext(t.id,function(){var isActive=normalizeConversationId(t.id)===normalizeConversationId(activeConversationId);if(j.todo_list&&j.todo_list.length){renderTodoListFromEvent({items:j.todo_list,all_done:j.todo_list.every(function(it){return !!it.done;}),collapsed:false},t.id);}else{hideTodoList();}if(items.length&&msgs.childNodes.length<=1){while(msgs.firstChild)msgs.removeChild(msgs.firstChild);renderHistoryItems(items);}else if(msgs.childNodes.length===0){renderHistoryItems(items);}if(isActive){scrollToBottomAfterLayout(msgs,true);}});}catch(e){}}
function _routeEventBySource(ev){var scid=ev&&ev._source_session;if(!scid)return;var cid=normalizeConversationId(scid);if(!cid)return;var tab=findConversationTab(cid);if(!tab)return;var t=ev.type;if(t==="assistant"){withConversationContext(cid,function(){var ac=String(ev.content||"").trim();if(ac){add("a",ac);scrollToBottomAfterLayout(msgs,true);}});}else if(t==="tool_start"){withConversationContext(cid,function(){onToolStart(ev);});}else if(t==="tool_end"){withConversationContext(cid,function(){onToolEnd(ev);if(ev.todo_list&&ev.todo_list_data){var _td=ev.todo_list_data;renderTodoListFromEvent({items:_td.items||[],all_done:Array.isArray(_td.items)&&_td.items.every(function(it){return !!it.done;}),collapsed:!!_td.collapsed,close:!!_td.close},cid);scrollToBottomAfterLayout(msgs,true);}});}else if(t==="todo_list"){if(ev.close){syncTodoUnreadBadgeForConversation(cid);}else if(ev.items){renderTodoListFromEvent({items:ev.items,all_done:Array.isArray(ev.items)&&ev.items.every(function(it){return !!it.done;}),collapsed:!!ev.collapsed,close:!!ev.close},cid);syncTodoUnreadBadgeForConversation(cid);}}else if(t==="context_layout"){var _t4=findConversationTab(cid);if(_t4)applyContextLayoutFromHistory(_t4,{context_layout:ev});}}
async function restoreConversationLayoutFromServer(){try{var r=await fetch("/api/chat/ui-state");if(!r.ok)return;var j=await r.json();var st=j&&j.state;if(!st||!Array.isArray(st.tabs)||!st.tabs.length){for(var i0=0;i0<conversationTabs.length;i0++)loadConversationHistory(conversationTabs[i0]);return;}saveActiveConversationView();conversationTabs=st.tabs.slice(-8).map(function(x){var id=normalizeConversationId(x&&x.id);var t=makeConversationTab(id||newConversationId());t.title=String(x&&x.title||"").slice(0,80)||t.title;return t;});var immPref=normalizeConversationId(function(){try{return sessionStorage.getItem(CONVERSATION_STORAGE_KEY)||"";}catch(e1){return"";}}());if(immPref&&!findConversationTab(immPref))ensureConversationTab(immPref);var srvA=normalizeConversationId(st.active_conversation_id)||(conversationTabs[0]&&conversationTabs[0].id)||"";var _idMap={};for(var _ti=0;_ti<conversationTabs.length;_ti++)_idMap[conversationTabs[_ti].id]=1;activeConversationId=(immPref&&_idMap[immPref])?immPref:srvA;sessionId=activeConversationId;conv=activeConversationId;storeConversationId(activeConversationId);restoreConversationView(getActiveTab());renderChatTabs();storeConversationLayoutLocal();for(var i=0;i<conversationTabs.length;i++)loadConversationHistory(conversationTabs[i]);}catch(e){for(var j2=0;j2<conversationTabs.length;j2++)loadConversationHistory(conversationTabs[j2]);}}
function syncTodoUnreadBadgeForConversation(cid){cid=normalizeConversationId(cid||"");if(!cid)return;var tb=findConversationTab(cid);if(!tb||!chatTabs)return;var want=!!tb.todoUnread;var b;var ch=chatTabs.children;var ui=0;for(;ui<ch.length;ui++){if(ch[ui]&&ch[ui].getAttribute("data-tab-id")===cid){b=ch[ui];break;}}if(!b){if(typeof renderChatTabs==="function")renderChatTabs();return;}if(b.classList.contains("chat-tab-todo-unread")===want)return;b.classList.toggle("chat-tab-todo-unread",want);renderChatTabs._sig=null;}
function renderChatTabs(){if(!chatTabs)return;var sig=conversationTabs.map(function(t){return String(t.id)+"\t"+String(t.title||"")+"\t"+(t.id===activeConversationId?1:0)+"\t"+(t.todoUnread?1:0);}).join("|");if(renderChatTabs._sig===sig&&chatTabs.children.length===conversationTabs.length){var mz=conversationTabs.length;for(var zp=0;zp<mz;zp++){var el=chatTabs.children[zp];if(!el||el.getAttribute("data-tab-id")!==conversationTabs[zp].id)break;}if(zp===mz)return;}renderChatTabs._sig=sig;var wc=conversationTabs.length>1;var pi,tid,t,btn,lab,labT,cn,ft,cur,kj,nel,xc;for(pi=0;pi<conversationTabs.length;pi++){t=conversationTabs[pi];tid=t.id;cur=chatTabs.children[pi];if(cur&&cur.getAttribute("data-tab-id")===tid)continue;btn=null;if(cur){for(kj=pi;kj<chatTabs.children.length;kj++){if(chatTabs.children[kj]&&chatTabs.children[kj].getAttribute("data-tab-id")===tid){btn=chatTabs.children[kj];break;}}}if(!btn){for(kj=0;kj<chatTabs.children.length;kj++){if(chatTabs.children[kj]&&chatTabs.children[kj].getAttribute("data-tab-id")===tid){btn=chatTabs.children[kj];break;}}}if(!btn){nel=document.createElement("button");nel.type="button";nel.setAttribute("role","tab");nel.setAttribute("data-tab-id",tid);(function(cid){nel.onclick=function(){switchConversationTab(cid);};})(tid);lab=document.createElement("span");lab.className="chat-tab-label";nel.appendChild(lab);btn=nel;}chatTabs.insertBefore(btn,cur||null);}while(chatTabs.children.length>conversationTabs.length){chatTabs.removeChild(chatTabs.lastChild);}for(pi=0;pi<conversationTabs.length;pi++){t=conversationTabs[pi];tid=t.id;btn=chatTabs.children[pi];if(!btn)continue;labT=t.title||("会话 "+t.id.slice(0,8));cn="chat-tab"+((t.id===activeConversationId)?" active":"")+(t.todoUnread?" chat-tab-todo-unread":"");ft="标题: "+labT+"\n会话ID: "+t.id;lab=btn.querySelector(".chat-tab-label");if(lab&&lab.textContent!==labT)lab.textContent=labT;if(btn.className!==cn)btn.className=cn;if(btn.title!==ft)btn.title=ft;xc=btn.querySelector(".chat-tab-close");if(wc){if(!xc){xc=document.createElement("button");xc.type="button";xc.className="chat-tab-close";xc.textContent="×";xc.title="关闭会话";btn.appendChild(xc);}(function(cid2){xc.onclick=function(ev){ev.stopPropagation();closeConversationTab(cid2);};})(tid);}else{if(xc)xc.remove();}}}
function switchConversationTab(id){id=normalizeConversationId(id);if(!id)return;saveActiveConversationView();var t=findConversationTab(id);if(!t)return;t.todoUnread=false;activeConversationId=id;sessionId=id;conv=id;storeConversationId(id);restoreConversationView(t);renderChatTabs();persistConversationLayout();loadConversationHistory(t);}
function createConversationTab(){saveActiveConversationView();var t=makeConversationTab(newConversationId());conversationTabs.push(t);activeConversationId=t.id;sessionId=t.id;conv=t.id;storeConversationId(t.id);restoreConversationView(t);renderChatTabs();persistConversationLayout();}
function closeConversationTab(id){id=normalizeConversationId(id);if(conversationTabs.length<=1)return;var idx=-1;for(var i=0;i<conversationTabs.length;i++){if(conversationTabs[i].id===id){idx=i;break;}}if(idx<0)return;if(isTabBusy(conversationTabs[idx])){alert("该会话正在响应或等待确认，请完成后再关闭。");return;}var wasActive=id===activeConversationId;var _closedT=conversationTabs[idx];conversationTabs.splice(idx,1);if(_closedT&&_closedT.conversationPane&&conversationMount&&_closedT.conversationPane.parentNode===conversationMount){try{conversationMount.removeChild(_closedT.conversationPane);}catch(_eM){}}if(wasActive){var next=conversationTabs[Math.max(0,idx-1)]||conversationTabs[0];activeConversationId=next.id;sessionId=next.id;conv=next.id;storeConversationId(next.id);restoreConversationView(next);}renderChatTabs();persistConversationLayout();}
const modePlus=document.getElementById("modePlus"),modeMenu=document.getElementById("modeMenu"),modeChip=document.getElementById("modeChip"),modeChipText=document.getElementById("modeChipText"),modeChipClear=document.getElementById("modeChipClear"),goBtn=document.getElementById("go"),stopTaskBtn=document.getElementById("stopTask"),chatMoreBtn=document.getElementById("chatMoreBtn"),chatSessionMenu=document.getElementById("chatSessionMenu");
let selectedMode="auto";let selectedModel="deepseek-v4-flash";const allowedModels=["deepseek-v4-pro","deepseek-v4-flash","glm-5.2","local-model"];
let slashSelectedIndex=0;
function updateTaskControls(){var t=getActiveTab();var canStop=!!(t&&t.abortController);if(goBtn){goBtn.classList.toggle("hidden",canStop);goBtn.disabled=!canStop&&isConversationBusy();}if(stopTaskBtn){stopTaskBtn.classList.toggle("hidden",!canStop);stopTaskBtn.disabled=!canStop;}}
function stopCurrentTask(){var t=getActiveTab();if(!t||!t.abortController)return;t.stopRequested=true;var rid=String(t.activeRunId||"");try{fetch("/api/chat/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:t.id,run_id:rid})}).catch(function(){});}catch(_e){}try{t.abortController.abort();}catch(_e2){}withConversationContext(t.id,function(){markCurrentTurnStoped();resetTurnState();hideChatLoading();add("a","任务已停止。");});t.abortController=null;t.activeRunId="";updateTaskControls();renderChatTabs();}

/* ---- @ 文件选择器 ---- */
var atSelectedIndex=-1;var atRestoreSelectPath=null;function atNormPath(s){return String(s||"").replace(/\\/g,"/").toLowerCase();}function getAtPop(){return document.getElementById("atPop");}function getAtList(){return document.getElementById("atList");}function getAtCurPath(){return document.getElementById("atCurPath");}function getAtUpBtn(){return document.getElementById("atUpBtn");}
function hideAtPop(){if(window.DEBUG)console.log("hideAtPop");var p=getAtPop();if(p){p.style.visibility="hidden";p.style.top="-99999px";p.setAttribute("aria-hidden","true");}atSelectedIndex=-1;atRestoreSelectPath=null;}
function atMentionActiveAtCursor(){var v=ta.value,sel=ta.selectionStart||0,before=v.slice(0,sel);var re=/(?:^|\s)@/g,m,last=null;while((m=re.exec(before))!==null)last=m;if(!last)return false;var atIdx=last.index+last[0].length-1;if(sel<=atIdx)return false;var seg=before.slice(atIdx+1,sel);if(/\s/.test(seg))return false;return true;}
function atLastTriggerIndexIn(t){var re=/(?:^|\s)@/g,m,last=-1;while((m=re.exec(t))!==null)last=m.index+m[0].length-1;return last;}
var ICON_MAP={txt:"📄",json:"📋",py:"🐍",java:"☕",html:"🌐",js:"📜",css:"🎨",xml:"📰",yml:"⚙️",yaml:"⚙️",properties:"🔧",sql:"🗄️",md:"📝",jpg:"🖼️",jpeg:"🖼️",png:"🖼️",gif:"🖼️",svg:"🖼️",bmp:"🖼️",xlsx:"📊",xls:"📊",docx:"📃",doc:"📃",pdf:"📕",pptx:"📽️",ppt:"📽️",zip:"📦",rar:"📦",exe:"⚙️",dll:"⚙️",log:"📋",csv:"📊",ts:"📜",tsx:"📜",jsx:"📜",vue:"📜",c:"📄",cpp:"📄",h:"📄",go:"📄",rs:"📄",kt:"📄",swift:"📄"};
function iconFor(ext){return ICON_MAP[ext]||"📄";}
async function loadAtDir(path){try{var resp=await fetch("/api/dir-browse?path="+encodeURIComponent(path||""));var data=await resp.json();renderAtList(data);}catch(e){atRestoreSelectPath=null;getAtList().innerHTML='<div class="at-item" style="color:#f66">加载失败: '+e.message+"</div>";}}
function renderAtList(data){getAtCurPath().textContent=data.current;getAtCurPath().dataset.parent=data.parent||"";getAtUpBtn().style.display=(!data.parent||data.parent===data.current)?"none":"";getAtList().innerHTML="";atSelectedIndex=-1;data.items.forEach(function(item,idx){var div=document.createElement("div");div.className="at-item"+(item.type==="dir"?" dir":"");div.id="atPop_item_"+idx;div.dataset.idx=idx;div.dataset.type=item.type;div.dataset.path=item.path;if(item.type==="dir")div.title="单击进入子目录；回车插入该目录路径";var icon=item.type==="dir"?"📁":iconFor(item.ext.replace(".",""));div.innerHTML='<span class="at-icon">'+icon+'</span><span class="at-name">'+item.name+"</span>";div.addEventListener("click",function(){if(item.type==="dir"){loadAtDir(item.path);}else{selectAtFile(item.path);}});div.addEventListener("mouseenter",function(){atSelectedIndex=idx;updateAtSelection();});getAtList().appendChild(div);});var pick=0;if(atRestoreSelectPath){var want=atRestoreSelectPath;atRestoreSelectPath=null;for(var rj=0;rj<data.items.length;rj++){if(data.items[rj].type==="dir"&&atNormPath(data.items[rj].path)===atNormPath(want)){pick=rj;break;}}}if(data.items&&data.items.length){atSelectedIndex=pick;updateAtSelection();}}
function updateAtSelection(){var items=getAtList().querySelectorAll(".at-item");items.forEach(function(it,idx){it.classList.toggle("selected",idx===atSelectedIndex);});}
function selectAtFile(fpath){var before=ta.value.slice(0,ta.selectionStart||0);var after=ta.value.slice(ta.selectionStart||0);var atIdx=atLastTriggerIndexIn(before);if(atIdx>=0){ta.value=before.slice(0,atIdx)+"@"+fpath+" "+after;}else{ta.value=before+"@"+fpath+" "+after;}hideAtPop();ta.focus();}
if(getAtUpBtn())getAtUpBtn().addEventListener("click",function(){var cur=getAtCurPath().textContent.trim();var parentStored=getAtCurPath().dataset.parent||"";if(!parentStored)return;var p=parentStored||cur.substring(0,cur.lastIndexOf("/"));if(!p||p.length<3){if(p&&p.length===2&&p.charAt(1)===":"){p=p+"/";}else{p=cur;}}if(p!==cur)atRestoreSelectPath=cur;loadAtDir(p);});
function showAtPop(){if(window.DEBUG)console.log("showAtPop");var p=getAtPop();if(!p)return;atSelectedIndex=-1;atRestoreSelectPath=null;loadAtDir("");p.style.visibility="visible";p.style.top="-306px";p.setAttribute("aria-hidden","false");}
/* ---- @ 文件选择器结束 ---- */
function hideSlashPop(){if(slashPop){slashPop.classList.add("hidden");slashPop.setAttribute("aria-hidden","true");}}
function positionSlashPop(){if(!ta||!slashPop)return;var rect=ta.getBoundingClientRect();var before=ta.value.slice(0,ta.selectionStart||0);var lineStart=before.lastIndexOf("\n")+1;var col=before.length-lineStart;var cs=getComputedStyle(ta);var fz=parseFloat(cs.fontSize)||13;var lh=parseFloat(cs.lineHeight);if(!isFinite(lh)||lh<=0)lh=fz*1.35;var padL=parseFloat(cs.paddingLeft)||4;var padT=parseFloat(cs.paddingTop)||2;var chW=fz*0.55*Math.max(col,0);var lines=(before.match(/\n/g)||[]).length;var x=rect.left+padL+chW+6;var y=rect.top+padT+lines*lh-slashPop.offsetHeight-6;if(y<8)y=rect.bottom+4;var maxx=window.innerWidth-slashPop.offsetWidth-8;x=Math.min(Math.max(8,x),maxx);slashPop.style.position="fixed";slashPop.style.left=x+"px";slashPop.style.top=y+"px";}
function autoResizeTextarea(){if(!ta)return;ta.style.height="auto";var maxH=parseInt(getComputedStyle(ta).maxHeight)||320;var newH=Math.min(ta.scrollHeight,maxH);ta.style.height=newH+"px";ta.style.overflowY=ta.scrollHeight>maxH?"auto":"hidden";}
function normalizeMode(v){const t=String(v||"auto").toLowerCase();return (t==="plan"||t==="execute"||t==="auto")?t:"auto";}
function applyMode(v){hideSlashPop();selectedMode=normalizeMode(v);const label=(selectedMode==="auto"?"Auto":(selectedMode==="plan"?"Plan":"Execute"));if(modePlus){modePlus.textContent=label+" +";modePlus.classList.remove("plan","execute");if(selectedMode==="plan")modePlus.classList.add("plan");if(selectedMode==="execute")modePlus.classList.add("execute");}if(modeChip){modeChip.classList.add("hidden");}if(modeChipText){modeChipText.textContent="";}}
function closeModeMenu(){modeMenu.classList.add("hidden");}
let reasoningEffort="high";let reasoningBtn=document.getElementById("reasoningBtn");let reasoningMenu=document.getElementById("reasoningMenu");let reasoningLabel=document.getElementById("reasoningLabel");
function applyReasoningEffort(effort){if(effort!=="high"&&effort!=="max")return;reasoningEffort=effort;if(reasoningLabel){reasoningLabel.textContent=effort==="high"?"High":"Max";reasoningLabel.style.color=effort==="max"?"#e8c98a":"inherit";reasoningBtn.style.borderColor=effort==="max"?"#8b6a2d":"";}if(reasoningMenu){var cs=reasoningMenu.querySelectorAll(".reasoning-choice");cs.forEach(function(c){c.classList.remove("reasoning-current");if(c.dataset.effort===effort)c.classList.add("reasoning-current");});}
var cid=activeConversationId;if(cid){fetch("/api/reasoning-effort",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:cid,effort:effort})}).catch(function(){});}}
function closeReasoningMenu(){if(reasoningMenu)reasoningMenu.classList.add("hidden");}
function initReasoningPicker(){if(!reasoningBtn||!reasoningMenu)return;
reasoningBtn.onclick=function(e){e.stopPropagation();closeModeMenu();closeModelSubmenu();reasoningMenu.classList.toggle("hidden");};
reasoningMenu.onclick=function(e){var t=e.target;if(!t||typeof t.closest!=="function")return;var btn=t.closest("[data-effort]");if(!btn||!btn.dataset)return;if(btn.dataset.effort){applyReasoningEffort(btn.dataset.effort);closeReasoningMenu();}};
document.addEventListener("click",function(e){if(reasoningMenu&&!reasoningMenu.classList.contains("hidden")&&!reasoningBtn.contains(e.target)&&!reasoningMenu.contains(e.target))closeReasoningMenu();});
fetch("/api/reasoning-effort?conversation_id="+encodeURIComponent(activeConversationId||"")).then(function(r){return r.json();}).then(function(d){if(d&&d.reasoning_effort)applyReasoningEffort(d.reasoning_effort);}).catch(function(){});}

function initModePicker(){
if(!modePlus||!modeMenu||!modeChip||!modeChipClear)return;
modePlus.onclick=function(e){e.stopPropagation();closeReasoningMenu();closeModelSubmenu();modeMenu.classList.toggle("hidden");};
modeMenu.onclick=function(e){
var t=e.target;if(!t||typeof t.closest!=="function")return;
var btn=t.closest("[data-mode],[data-action],[data-model]");
if(!btn||!btn.dataset)return;
if(btn.dataset.mode){applyMode(btn.dataset.mode);closeModeMenu();closeModelSubmenu();return;}
if(btn.dataset.action==="model-menu"){e.stopPropagation();if(modelSubmenu)modelSubmenu.classList.toggle("hidden");return;}
if(btn.dataset.model){applyModel(btn.dataset.model);closeModeMenu();closeModelSubmenu();return;}
};
modeChipClear.onclick=function(e){e.stopPropagation();applyMode("auto");};
document.addEventListener("click",function(e){
if(!modeMenu.contains(e.target)&&e.target!==modePlus)closeModeMenu();
if(modelSubWrap&&modelSubmenu&&!modelSubmenu.classList.contains("hidden")&&!modelSubWrap.contains(e.target))closeModelSubmenu();
if(slashPop&&!slashPop.classList.contains("hidden")&&!slashPop.contains(e.target)&&e.target!==ta)hideSlashPop();
if(chatSessionMenu&&!chatSessionMenu.classList.contains("hidden")&&!chatSessionMenu.contains(e.target)&&e.target!==chatMoreBtn)chatSessionMenu.classList.add("hidden");
});
applyMode("auto");
}
function closeModelSubmenu(){if(modelSubmenu)modelSubmenu.classList.add("hidden");}
function applyModel(m){
var x=String(m||"").trim();if(allowedModels.indexOf(x)<0)return;
if(selectedModel!==x){pricingRates=null;pricingFailed=false;pricingCacheKey="";}
selectedModel=x;var hm=document.getElementById("hdrModel");if(hm)hm.textContent="模型: "+selectedModel;
document.querySelectorAll(".model-choice").forEach(function(b){if(b&&b.dataset&&b.dataset.model)b.classList.toggle("model-current",b.dataset.model===selectedModel);});
if(conv)void ensureModelPricing();
}
function selectSidePane(name){
document.querySelectorAll(".side-tab").forEach(function(el){el.classList.toggle("active",el.dataset.pane===name);});
if(paneSteps)paneSteps.classList.toggle("active",name==="steps");
if(paneKb)paneKb.classList.toggle("active",name==="kb");
}
function openKbTab(){
if(!sideTabbar||!paneKb)return;
var ex=document.getElementById("tabKb");
if(!ex){
var w=document.createElement("div");w.className="side-tab";w.id="tabKb";w.dataset.pane="kb";w.setAttribute("role","tab");w.tabIndex=0;
var sp=document.createElement("span");sp.textContent="知识库";sp.style.flex="1";w.appendChild(sp);
var x=document.createElement("button");x.type="button";x.className="tab-x";x.textContent="×";x.title="关闭";
x.addEventListener("click",function(ev){ev.stopPropagation();closeKbTab();});
w.appendChild(x);w.addEventListener("click",function(ev){if(ev.target===x)return;selectSidePane("kb");});
sideTabbar.appendChild(w);
}
selectSidePane("kb");
renderKbPanel();
}
function closeKbTab(){
var t=document.getElementById("tabKb");if(t)t.remove();
selectSidePane("steps");
}
function renderKbPanel(){
var body=document.getElementById("kbBody");
if(!body)return;
body.innerHTML='<div class="kb-loading">📂 加载知识库…</div>';
fetch("/api/kb/files").then(function(r){return r.json();}).then(function(data){
if(!data.ok||!data.enabled){
body.innerHTML='<div class="kb-empty">⚠️ 知识库未启用<br><span class="kb-hint">请在 config.json 中配置 KNOWLEDGE_BASE_DIR</span></div>';
return;
}
if(!data.files||!data.files.length){
body.innerHTML='<div class="kb-empty">📭 知识库目录为空</div>';
return;
}
var cid=getActiveConversationId();
fetch("/api/kb/checked?conversation_id="+encodeURIComponent(cid)).then(function(r2){return r2.json();}).then(function(sd){
var cs={};if(sd.ok&&sd.checked){sd.checked.forEach(function(p){cs[p]=true;});}
renderKbFileList(body,data.files,cs,cid);
}).catch(function(){renderKbFileList(body,data.files,{},cid);});
}).catch(function(err){
body.innerHTML='<div class="kb-empty">❌ 加载失败: '+(err.message||String(err))+'</div>';
});
}
function escapeHtml(s){if(!s)return"";return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function renderKbFileList(container,files,checkedSet,cid){
var html='<div class="kb-header"><span class="kb-count">共 '+files.length+' 个文件</span></div><div class="kb-hint-bar">💡 勾选的文件将作为当前会话的上下文参考内容</div><div class="kb-list">';
for(var i=0;i<files.length;i++){
var f=files[i];
var ch=checkedSet[f.path]?' checked':'';
html+='<label class="kb-file-row'+ch+'">';
html+='<span class="kb-cb'+(checkedSet[f.path]?' checked':'')+'" data-path="'+escapeHtml(f.path)+'"></span>';
html+='<span class="kb-fpath">'+escapeHtml(f.path)+'</span>';
html+='</label>';
}
html+='</div>';
container.innerHTML=html;
container.querySelectorAll(".kb-file-row").forEach(function(row){
row.addEventListener("click",function(){
var cb=row.querySelector(".kb-cb");if(!cb)return;
var was=cb.classList.contains("checked");
if(was){cb.classList.remove("checked");row.classList.remove("checked");}else{cb.classList.add("checked");row.classList.add("checked");}
persistKbChecked(cid);
});
});
}
function persistKbChecked(cid){
var checked=[];
document.querySelectorAll("#kbBody .kb-cb.checked").forEach(function(cb){
var p=cb.getAttribute("data-path");if(p)checked.push(p);
});
fetch("/api/kb/checked",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:cid,checked:checked})}).then(function(r){return r.json();}).then(function(j){
if(!j||!j.ok||!Array.isArray(j.checked))return;
var set={};j.checked.forEach(function(p){set[p]=true;});
document.querySelectorAll("#kbBody .kb-cb").forEach(function(cb){
var p=cb.getAttribute("data-path");var row=cb.closest(".kb-file-row");if(!row)return;
if(set[p]){cb.classList.add("checked");row.classList.add("checked");}
else{cb.classList.remove("checked");row.classList.remove("checked");}
});
}).catch(function(){});
}
async function loadHealthModel(){
try{var r=await fetch("/health");if(!r.ok)return;var j=await r.json();if(j.model)applyModel(j.model);}catch(e){}
}
function initKbAndSlashUi(){
if(ta&&slashPop){
slashPop.addEventListener("click",function(e){
var b=e.target.closest(".slash-item");if(!b)return;e.stopPropagation();hideSlashPop();ta.value="";
var s=b.getAttribute("data-slash");if(s==="auto")applyMode("auto");else if(s==="plan")applyMode("plan");else if(s==="execute")applyMode("execute");
});
ta.addEventListener("keyup",function(e){if(e.isComposing)return;if(e.key==="Escape"){hideSlashPop();hideAtPop();return;}
if(e.key==="/"&&document.activeElement===ta&&ta.value==="/"){var ci=slashPop.children;for(var i=0;i<ci.length;i++){ci[i].classList.remove("selected");ci[i].style.display=(ci[i].dataset.slash===selectedMode?"none":"");}for(var i=0;i<ci.length;i++){if(ci[i].style.display!=="none"){ci[i].classList.add("selected");slashSelectedIndex=i;break;}}updateSlashPopHints();positionSlashPop();slashPop.classList.remove("hidden");slashPop.setAttribute("aria-hidden","false");}
});
ta.addEventListener("input",function(){autoResizeTextarea();var ap=getAtPop();var vis=ap&&ap.style.visibility!=="hidden";if(atMentionActiveAtCursor()&&(!ap||!vis)){showAtPop();}else if(vis&&!atMentionActiveAtCursor()){hideAtPop();}if(ta.value!=="/")hideSlashPop();});
document.addEventListener("mousedown",function(ev){var ap2=getAtPop();if(!ap2||ap2.style.visibility==="hidden")return;if(ap2.contains(ev.target))return;hideAtPop();},true);
}
document.getElementById("kbBtn")?.addEventListener("click",openKbTab);
void loadHealthModel();
}
function updateSlashPopHints(){
var items=slashPop.querySelectorAll(".slash-item");var visibles=[];
for(var i=0;i<items.length;i++){if(items[i].style.display!=="none")visibles.push(items[i]);}
if(!visibles.length)return;var selIdx=-1;
for(var i=0;i<visibles.length;i++){if(visibles[i].classList.contains("selected")){selIdx=i;break;}}
if(selIdx<0){visibles[0].classList.add("selected");selIdx=0;}
for(var i=0;i<visibles.length;i++){
var sh=visibles[i].querySelector(".sh");if(!sh)continue;
var sk=(visibles[i].dataset.slash||"").charAt(0).toUpperCase();
var hint;
if(i===selIdx)hint="Enter";
else if(i<selIdx)hint="↑ + Enter";
else hint="↓ + Enter";
sh.textContent=sk+"  "+hint;
}
}

function escapeHtml(s){
return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function highlightJson(s){
if(window.codeHighlight)return window.codeHighlight.highlightCode(s,'json');
var t=String(s||'');try{var o=JSON.parse(t);t=JSON.stringify(o,null,2);}catch(e){}
return t.replace(/("(?:\\.|[^"\\])*")\s*:/g,'<span style="color:#ce9178;">$1</span>:')
.replace(/:\s*("(?:\\.|[^"\\])*")/g,':<span style="color:#ce9178;">$1</span>')
.replace(/:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,':<span style="color:#b5cea8;">$1</span>')
.replace(/:\s*(true|false|null)/g,':<span style="color:#569cd6;">$1</span>');}
function highlightCode(s,lang){
if(window.codeHighlight)return window.codeHighlight.highlightCode(s,lang);
var t=String(s||'');
if(String(lang||'').toLowerCase()==='json'||(!lang&&/^\s*[\[{]/.test(t)))return highlightJson(t);
return escapeHtml(t);
}
function detectScriptLang(code,hint){
if(window.codeHighlight)return window.codeHighlight.detectScriptLang(code,hint);
return 'python';
}
function hljsCodeClass(lang){
if(window.codeHighlight)return window.codeHighlight.hljsCodeClass(lang);
return '';
}
function renderInlineMarkdown(s){
let t=escapeHtml(s||'');
// 裸 Windows 路径 D:\...\kling_tasks\... → 转 /kling-tasks/
t=t.replace(/!\[([^\]]*)\]\(([a-zA-Z]:[\/\\]AI_DATA_ROOT[\/\\]workspace[\/\\]([^\s)]+))\)/g,function(_,alt,raw,p){var localPath=raw.replace(/\//g,'\\');if(/\.(mp4|mov|webm|avi)(\?|$)/i.test(p))return '<video src="/workspace/'+p+'" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;" title="'+localPath+'" alt="'+localPath+'"></video>';return '<img src="/workspace/'+p+'" alt="'+localPath+'" loading="lazy" style="max-width:300px;height:auto" title="'+localPath+'" />';});
// file:/// 工作区路径 → 转 /kling-tasks/ 静态路由
t=t.replace(/!\[([^\]]*)\]\(file:\/\/\/[a-zA-Z]:[\/\\]AI_DATA_ROOT[\/\\]workspace[\/\\]([^\s)]+)\)/g,function(_,alt,p){var localPath='D:\\AI_DATA_ROOT\\workspace\\'+p.replace(/\//g,'\\');if(/\.(mp4|mov|webm|avi)(\?|$)/i.test(p))return '<video src="/workspace/'+p+'" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;" title="'+localPath+'" alt="'+localPath+'"></video>';return '<img src="/workspace/'+p+'" alt="'+localPath+'" loading="lazy" style="max-width:300px;height:auto" title="'+localPath+'" />';});
t=t.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+|\/[^\s)]+|[a-zA-Z]:[\/\\][^\s)]+)\)/g,function(_,alt,url){if(/\.(mp4|mov|webm|avi)(\?|$)/i.test(url))return '<video src="'+url+'" controls style="max-width:100%;border-radius:6px;margin:4px 0;max-height:480px;background:#000;"></video>';return '<img src="'+url+'" alt="'+alt+'" loading="lazy" style="max-width:300px;height:auto" />';});t=t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,function(_,label,url){return '<a href="'+url+'" target="_blank" rel="noopener noreferrer">'+label+'</a>';});
t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
t=t.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
t=t.replace(/__([^_]+)__/g,'<strong>$1</strong>');
t=t.replace(/~~([^~]+)~~/g,'<del>$1</del>');
t=t.replace(/(^|[^*])\*([^*]+)\*/g,'$1<em>$2</em>');
t=t.replace(/(^|[\s>]|[([{])_([a-zA-Z0-9]+)_([\s<.,!?;:)\]}>-]|$)/g,'$1<em>$2</em>$3');
return t;
}
function closeOpenLists(state){
let html='';
while(state.stack.length){html+='</'+state.stack.pop()+'>';}
return html;
}
function ensureListLevel(state,targetTag,targetDepth){
let html='';
while(state.stack.length>targetDepth){html+='</'+state.stack.pop()+'>';}
while(state.stack.length<targetDepth){state.stack.push(targetTag);html+='<'+targetTag+'>';}
if(state.stack.length&&state.stack[state.stack.length-1]!==targetTag){html+='</'+state.stack.pop()+'>';state.stack.push(targetTag);html+='<'+targetTag+'>';}
return html;
}
function parseTable(lines,i){
if(i+1>=lines.length)return null;
const head=lines[i];
const sep=lines[i+1];
if(head.indexOf('|')<0||sep.indexOf('|')<0)return null;
const sepClean=sep.trim().replace(/^\||\|$/g,'');
const cols=sepClean.split('|').map(function(x){return x.trim();});
if(!cols.length||!cols.every(function(c){return /^:?-{1,}:?$/.test(c);})){return null;}
const headers=head.trim().replace(/^\||\|$/g,'').split('|').map(function(x){return x.trim();});
let j=i+2;
const rows=[];
while(j<lines.length&&lines[j].indexOf('|')>=0&&lines[j].trim()!==''){
rows.push(lines[j].trim().replace(/^\||\|$/g,'').split('|').map(function(x){return x.trim();}));
j++;
}
let html='<table><thead><tr>';
for(let k=0;k<headers.length;k++){html+='<th>'+renderInlineMarkdown(headers[k]||'')+'</th>';}
html+='</tr></thead>';
if(rows.length){html+='<tbody>';for(const r of rows){html+='<tr>';for(let k=0;k<headers.length;k++){html+='<td>'+renderInlineMarkdown(r[k]||'')+'</td>';}html+='</tr>';}html+='</tbody>';}
html+='</table>';
return {html:html,next:j};
}
function renderMarkdownBlocks(seg){
const lines=String(seg||'').replace(/\r/g,'').split('\n');
let html='';
const listState={stack:[]};
let quoteDepth=0;
function closeQuotes(target){
let out='';
while(quoteDepth>target){out+='</blockquote>';quoteDepth--;}
while(quoteDepth<target){out+='<blockquote>';quoteDepth++;}
return out;
}
for(let i=0;i<lines.length;i++){
let line=lines[i];
if(!line.trim()){
continue;
}
let quote=0;
while(/^\s*>/.test(line)){line=line.replace(/^\s*>\s?/,'');quote++;}
html+=closeQuotes(quote);
if(/^\s*([-*_])\s*\1\s*\1[-*_\s]*$/.test(line)){
html+=closeOpenLists(listState);
html+='<hr/>';
continue;
}
const table=parseTable(lines,i);
if(table){
html+=closeOpenLists(listState);
html+=table.html;
i=table.next-1;
continue;
}
let m=line.match(/^(#{1,6})\s+(.+)$/);
if(m){
html+=closeOpenLists(listState);
const lv=m[1].length;
html+='<h'+lv+'>'+renderInlineMarkdown(m[2])+'</h'+lv+'>';
continue;
}
m=line.match(/^\s*(\d+)\.\s+\[( |x|X)\]\s+(.+)$/);
if(m){
const depth=Math.floor((line.match(/^\s*/)||[''])[0].length/2)+1;
html+=ensureListLevel(listState,'ol',depth);
const checked=(m[2].toLowerCase()==='x')?' checked':'';
html+='<li class="task-item"><input type="checkbox" disabled'+checked+'/><span>'+renderInlineMarkdown(m[3])+'</span></li>';
continue;
}
    if(shouldStartUnifiedDiffBlock(lines,i)){
    var _ud=collectUnifiedDiffBlock(lines,i);
    if(looksLikeUnifiedDiffBlock(_ud.lines)){
    html+=closeOpenLists(listState);
    html+=renderUnifiedDiffBlockHtml(_ud.lines);
    i=_ud.next-1;
    continue;
    }
    }
    
m=line.match(/^\s*[-*+]\s+(.+)$/);
if(m){
const depth=Math.floor((line.match(/^\s*/)||[''])[0].length/2)+1;
html+=ensureListLevel(listState,'ul',depth);
html+='<li>'+renderInlineMarkdown(m[1])+'</li>';
continue;
}
m=line.match(/^\s*\d+\.\s+(.+)$/);
if(m){
const depth=Math.floor((line.match(/^\s*/)||[''])[0].length/2)+1;
html+=ensureListLevel(listState,'ol',depth);
html+='<li>'+renderInlineMarkdown(m[1])+'</li>';
continue;
}
html+=closeOpenLists(listState);
html+='<p>'+renderInlineMarkdown(line)+'</p>';
}
html+=closeOpenLists(listState);
html+=closeQuotes(0);
return html;
}
function isUnifiedDiffLine(line){
var s=String(line||"");
if(!s)return false;
if(/^---\s/.test(s)||/^\+\+\+\s/.test(s)||/^@@\s/.test(s))return true;
if(/^[-+ ]/.test(s))return true;
return false;
}
function shouldStartUnifiedDiffBlock(lines,i){
if(i>=lines.length||!isUnifiedDiffLine(lines[i]))return false;
if(/^---\s/.test(lines[i])||/^@@\s/.test(lines[i]))return true;
if(i+1>=lines.length)return/^[-+]/.test(lines[i]);
return isUnifiedDiffLine(lines[i+1]);
}
function collectUnifiedDiffBlock(lines,start){
var block=[],i=start;
while(i<lines.length){
var line=lines[i];
if(isUnifiedDiffLine(line)){block.push(line);i++;continue;}
if(!String(line).trim()&&i+1<lines.length&&isUnifiedDiffLine(lines[i+1])){block.push(line);i++;continue;}
break;
}
return {lines:block,next:i};
}
function looksLikeUnifiedDiffBlock(block){
if(!block||block.length<2)return false;
var hasMarker=false,hasChange=false;
for(var j=0;j<block.length;j++){
var L=block[j];
if(/^---\s/.test(L)||/^\+\+\+\s/.test(L)||/^@@\s/.test(L))hasMarker=true;
if((/^-/.test(L)&&!/^---/.test(L))||(/^\+/.test(L)&&!/^\+\+\+/.test(L)))hasChange=true;
}
return hasMarker;
}
function renderUnifiedDiffBlockHtml(block){
var body=block.join("\n");
var cards=renderUnifiedDiffBodyAsCardsHtml(body);
if(cards)return cards;
return '<pre><code>'+escapeHtml(body)+'</code></pre>';
}
function isDevNullPath(p){
var s=String(p||"").trim().replace(/\\/g,"/").toLowerCase();
if(!s||s==="null")return true;
return s==="/dev/null"||s==="dev/null"||s.endsWith("/dev/null");
}
function parseDiffHeaderPath(line){
var m=/^(---|\+\+\+)\s+(.+)$/.exec(String(line||"").trim());
if(!m)return "";
return m[2].split("\t")[0].trim();
}
function diffFileNameFromBody(body){
var lines=String(body||'').replace(/\r/g,'').split('\n');
var fromRaw="",toRaw="";
for(var i=0;i<lines.length;i++){
var L=lines[i];
if(!fromRaw&&L.indexOf("--- ")===0)fromRaw=parseDiffHeaderPath(L);
if(!toRaw&&L.indexOf("+++ ")===0)toRaw=parseDiffHeaderPath(L);
}
if(toRaw&&!isDevNullPath(toRaw))return baseNameOnly(toRaw);
if(fromRaw&&!isDevNullPath(fromRaw))return baseNameOnly(fromRaw);
return "文件";
}
function findDiffFenceCloseIndex(rest){
var reLine=/^[ \t]*```[ \t]*$/gm,m;
while((m=reLine.exec(rest))!==null){
var after=rest.slice(m.index+m[0].length);
var trimmed=after.replace(/^\r?\n/,"");
if(!trimmed.trim())return m.index;
if(/^```(?:diff|patch)\b/i.test(trimmed))return m.index;
var firstLine=(trimmed.split(/\r?\n/)[0]||"").trim();
if(/^---\s/.test(firstLine)||/^\+\+\+\s/.test(firstLine)||/^@@\s/.test(firstLine))continue;
if(/^[-+ ]/.test(firstLine))continue;
return m.index;
}
return -1;
}
function splitUnifiedDiffSections(body){
var lines=String(body||"").replace(/\r/g,"").split("\n");
var sections=[],cur=[];
for(var i=0;i<lines.length;i++){
var L=lines[i];
if(/^---\s/.test(L)&&cur.some(function(x){return /^---\s/.test(x);})){
sections.push(cur.join("\n"));
cur=[L];
continue;
}
cur.push(L);
}
if(cur.length)sections.push(cur.join("\n"));
return sections.filter(function(s){return String(s).trim();});
}
function renderUnifiedDiffBodyAsCardsHtml(body){
var sections=splitUnifiedDiffSections(body);
if(!sections.length)return "";
if(sections.length===1){
var pr=parseUnifiedDiffBodyForRows(body);
if(pr.oldT!==""||pr.newT!==""){
return buildChatDiffCardHtml(pr.oldT,pr.newT,diffFileNameFromBody(body));
}
return "";
}
var html="";
for(var si=0;si<sections.length;si++){
var sec=sections[si];
var pr2=parseUnifiedDiffBodyForRows(sec);
if(pr2.oldT===""&&pr2.newT==="")continue;
html+=buildChatDiffCardHtml(pr2.oldT,pr2.newT,diffFileNameFromBody(sec));
}
return html;
}
function iterMarkdownCodeFences(text,visit){
var src=String(text||"").replace(/\r\n/g,"\n");
var i=0;
while(i<src.length){
var open=src.indexOf("```",i);
if(open<0){visit("text",src.slice(i));break;}
if(open>i)visit("text",src.slice(i,open));
var langEnd=src.indexOf("\n",open+3);
if(langEnd<0){visit("text",src.slice(open));break;}
var lang=src.slice(open+3,langEnd);
var langLow=lang.trim().toLowerCase();
var pos=langEnd+1;
var closed=false;
if(langLow==="diff"||langLow==="patch"){
var rest=src.slice(pos);
var closeAt=findDiffFenceCloseIndex(rest);
if(closeAt>=0){
var closeMatch=rest.slice(closeAt).match(/^[ \t]*```/);
visit("fence",lang,rest.slice(0,closeAt).replace(/\n$/,""));
i=pos+closeAt+closeMatch[0].length;
var nl=src.indexOf("\n",i);
i=nl<0?src.length:nl+1;
closed=true;
}
}else{
while(pos<src.length){
var idx=src.indexOf("```",pos);
if(idx<0)break;
var lineStart=src.lastIndexOf("\n",idx-1)+1;
if(lineStart!==idx){pos=idx+3;continue;}
var lineEnd=src.indexOf("\n",idx);
if(lineEnd<0)lineEnd=src.length;
var line=src.slice(lineStart,lineEnd);
if(/^[ \t]*```[ \t]*$/.test(line)){
visit("fence",lang,src.slice(langEnd+1,lineStart).replace(/\n$/,""));
i=lineEnd<src.length?lineEnd+1:src.length;
closed=true;
break;
}
pos=idx+3;
}
}
if(!closed){visit("text",src.slice(open));break;}
}
}
function parseUnifiedDiffBodyForRows(body){
var lines=String(body||'').replace(/\r/g,'').split('\n');
var oldL=[],newL=[];
for(var i=0;i<lines.length;i++){
var L=lines[i];
if(/^---/.test(L)||/^\+\+\+/.test(L))continue;
if(/^@@/.test(L))continue;
if(/^\s*#/.test(L))continue;
if(/^-/.test(L)&&!/^---/.test(L))oldL.push(L.slice(1));
else if(/^\+/.test(L)&&!/^\+\+\+/.test(L))newL.push(L.slice(1));
else if(/^ /.test(L)){var c=L.slice(1);oldL.push(c);newL.push(c);}
}
return {oldT:oldL.join('\n'),newT:newL.join('\n')};
}
function diffRowInnerHtml(r){
var g=r.t==='-'?'−':(r.t==='+'?'+':' ');
var gs=r.t==='-'?'d-gutter-del':(r.t==='+'?'d-gutter-add':'d-gutter-eq');
return '<span class="d-gutter '+gs+'">'+escapeHtml(g)+'</span><span class="d-code">'+escapeHtml(r.l)+'</span>';
}
function buildDiffRowsHtml(oldT,newT){
var rows=linesDiff(oldT,newT);
var html='';
for(var ri=0;ri<rows.length;ri++){
var r=rows[ri];
var cls=r.t==='-'?'d-del':(r.t==='+'?'d-add':'d-eq');
html+='<div class="'+cls+'">'+diffRowInnerHtml(r)+'</div>';
}
return html;
}
function buildChatDiffCardHtml(oldT,newT,nameHint){
var st=calcDiffStats(oldT,newT);
var fn=String(nameHint||'').trim()||'文件';
var cap='<div class="chat-diff-cap">'+escapeHtml(fn)+' · diff';
if(st.del>0)cap+=' <span class="chat-diff-neg">-'+st.del+'</span>';
if(st.add>0)cap+=' <span class="chat-diff-pos">+'+st.add+'</span>';
cap+='</div>';
var box='<div class="diff-unified diff-surface-adaptive">'+buildDiffRowsHtml(oldT,newT)+'</div>';
return '<div class="chat-diff-card">'+cap+box+'</div>';
}
function renderMarkdown(md){
const text=String(md||'').replace(/\r\n/g,'\n');
let html='';
iterMarkdownCodeFences(text,function(kind,a,b){
if(kind==='text'){html+=renderMarkdownBlocks(a);return;}
const lang=String(a||'').trim();
const inner=b;
const langLow=lang.toLowerCase();
if((langLow==='diff'||langLow==='patch')&&inner){
var cards=renderUnifiedDiffBodyAsCardsHtml(inner);
if(cards){
html+=cards;
}else{
var _hc=hljsCodeClass(lang);html+='<pre><code'+(_hc?' class="'+_hc+'"':'')+(lang?' data-lang="'+escapeHtml(lang)+'"':'')+'>'+highlightCode(inner,lang)+'</code></pre>';
}
}else{
var _hc2=hljsCodeClass(lang);html+='<pre><code'+(_hc2?' class="'+_hc2+'"':'')+(lang?' data-lang="'+escapeHtml(lang)+'"':'')+'>'+highlightCode(inner,lang)+'</code></pre>';
}
});
return html||'<p></p>';
}
function add(role,t){
if(!msgs)return null;
const e=document.createElement('div');
e.className='b '+(role==='u'?'u':'a');
if(role==='u'){e.textContent=t;}else{e.innerHTML=renderMarkdown(t||'');}
msgs.appendChild(e);
scrollMsgsToBottom();
return e;
}
function _peerAvatarLetter(name){
var s=String(name||"A").trim();
return s?s.charAt(0).toUpperCase():"A";
}
function _buildPeerMetaHtml(metaRaw){
var line=String(metaRaw||"").trim().replace(/\|/g," ");
if(!line)return"";
return'<span class="peer-meta-tag">'+escapeHtml(line)+"</span>";
}
function addPeerMessage(t,name,cid){
if(!msgs)return;
var label=String(name||cid||"Agent");
var content=t||"";
var metaHtml="";
if(content.indexOf("[from=")===0){var ci=content.indexOf("]");if(ci>0){var metaRaw=content.slice(1,ci);content=content.slice(ci+1).trim();metaHtml=_buildPeerMetaHtml(metaRaw);}}
var e=document.createElement("div");
e.className="peer-chat-row";
var nameLine=escapeHtml(label);
if(cid&&cid!==label)nameLine+=' <span class="peer-agent-cid">'+escapeHtml(String(cid).slice(0,8))+'</span>';
e.innerHTML=
'<div class="peer-top">'+
'<div class="peer-avatar" title="'+escapeHtml(label)+'">'+escapeHtml(_peerAvatarLetter(label))+'</div>'+
'<div class="peer-top-text">'+
'<div class="peer-agent-name">'+nameLine+'</div>'+
(metaHtml||'')+
'</div></div>'+
'<div class="peer-bubble"><div class="peer-agent-body b a"></div></div>';
var bodyEl=e.querySelector(".peer-agent-body");
if(bodyEl){bodyEl.innerHTML=renderMarkdown(content||"");}
msgs.appendChild(e);
scrollMsgsToBottom();
}

function ensureAssistantStreamBubble(){
if(streamAssistantEl)return streamAssistantEl;
if(!msgs)return null;
const e=document.createElement('div');
e.className='b a';
e.innerHTML='';
msgs.appendChild(e);
scrollMsgsToBottom();
streamAssistantEl=e;
streamAssistantText='';
return e;
}
function appendAssistantDelta(text){
if(typeof text!=='string'||!text)return;
const e=ensureAssistantStreamBubble();if(!e)return;
if(pendingDeltaSeparator&&streamAssistantText){
streamAssistantText+='\n\n';
}
pendingDeltaSeparator=false;
streamAssistantText+=text;
e.innerHTML=renderMarkdown(streamAssistantText);
scrollMsgsToBottom();
}
function finalizeAssistantStream(content){
if(streamAssistantEl){
if(typeof content==='string'&&content){
streamAssistantText=String(content);
streamAssistantEl.innerHTML=renderMarkdown(streamAssistantText);
}
streamAssistantEl=null;
streamAssistantText='';
pendingDeltaSeparator=false;
scrollMsgsToBottom();
return true;
}
return false;
}
function appendStep(el){steps.appendChild(el);scrollToBottom(steps);}
function findStepCardForToolCall(tid){
var id=String(tid!=null?tid:"").trim();
if(!id)return null;
var _q=id.replace(/\\/g,"\\\\").replace(/"/g,'\\"');
var sel='.step.card[data-tool-call-id="'+_q+'"]';
if(steps){var c0=steps.querySelector(sel);if(c0)return c0;}
if(typeof stepsRoot!=="undefined"&&stepsRoot){var c1=stepsRoot.querySelector(sel);if(c1)return c1;}
if(typeof conversationTabs!=="undefined"&&conversationTabs&&conversationTabs.length){
for(var i=0;i<conversationTabs.length;i++){var sh=conversationTabs[i]&&conversationTabs[i].stepsHost;if(sh){var c2=sh.querySelector(sel);if(c2)return c2;}}
}
try{var c3=document.querySelector(sel);if(c3)return c3;}catch(_e){}
return null;
}
function flushPendingSteps(){while(pendingStepEls.length){appendStep(pendingStepEls.shift());}}function discardPendingSteps(){clearLlmAnim();pendingStepEls=[];lastLlm=null;}
function addDispatchTitle(title){
const t=String(title||"").trim();
if(!t)return;
if(seenDispatchTitle===t)return;
seenDispatchTitle=t;
const d=document.createElement("div");d.className="dispatch-title";
d.textContent="本轮调度："+t;
appendStep(d);
}
function tokenPercent(){if(SESSION_TOKEN_LIMIT<=0)return 0;const p=Math.ceil(((sessionTokenUsed%SESSION_TOKEN_LIMIT)/SESSION_TOKEN_LIMIT)*100);if(!isFinite(p)||p<0)return 0;return p>100?100:p;}
/** 上下文占比展示：百分数向上取到 2 位小数再格式化（如 0.001% → 0.01%） */
function formatContextPct(p){var x=Number(p)||0;if(x<=0)return"0.00";var v=Math.ceil(x*100)/100;return v.toFixed(2);}
function ensureCtxLayoutTooltip(){if(ctxLayoutTooltipEl)return ctxLayoutTooltipEl;ctxLayoutTooltipEl=document.createElement("div");ctxLayoutTooltipEl.id="ctx-layout-tooltip";ctxLayoutTooltipEl.className="ctx-layout-tooltip";ctxLayoutTooltipEl.style.display="none";ctxLayoutTooltipEl.setAttribute("role","tooltip");document.body.appendChild(ctxLayoutTooltipEl);if(!ctxLayoutTooltipInited){ctxLayoutTooltipInited=true;ctxLayoutTooltipEl.addEventListener("mouseenter",function(){if(ctxLayoutTipHideTimer){clearTimeout(ctxLayoutTipHideTimer);ctxLayoutTipHideTimer=null;}});ctxLayoutTooltipEl.addEventListener("mouseleave",function(){scheduleHideCtxLayoutTooltip();});}return ctxLayoutTooltipEl;}
function scheduleHideCtxLayoutTooltip(){var tt=ctxLayoutTooltipEl;if(!tt)return;if(ctxLayoutTipHideTimer)clearTimeout(ctxLayoutTipHideTimer);ctxLayoutTipHideTimer=setTimeout(function(){ctxLayoutTipHideTimer=null;tt.style.display="none";},160);}
function positionCtxLayoutTooltip(tt, anchorEl){
if(!tt||!anchorEl)return;
tt.style.position="fixed";
tt.style.transform="";
tt.style.bottom="auto";
var r=anchorEl.getBoundingClientRect();
var left=r.left;
var gap=10;
tt.style.left=left+"px";
tt.style.zIndex="10050";
requestAnimationFrame(function(){
void tt.offsetHeight;
var h=tt.offsetHeight||0;
var top=r.top-h-gap;
if(top<8)top=8;
tt.style.top=top+"px";
var vw=document.documentElement.clientWidth||window.innerWidth;
var tw=tt.offsetWidth||0;
if(left+tw>vw-8)tt.style.left=Math.max(8,vw-tw-8)+"px";
});
}
function buildCtxLayoutTooltipHtml(segs, colors){
var rows=[];
for(var i=0;i<segs.length;i++){var s=segs[i];if(!s)continue;rows.push(s);}
var maxTok=0;
for(var j=0;j<rows.length;j++)maxTok=Math.max(maxTok,Math.max(0,Math.floor(Number(rows[j].tokens)||0)));
if(maxTok<1)maxTok=1;
var trackW=120;
var parts=[];
for(var m=0;m<rows.length;m++){
var sg=rows[m];
var key=String(sg.key||"");
var lab=String(sg.label||key);
var tok=Math.max(0,Math.floor(Number(sg.tokens)||0));
var pct=formatContextPct(sg.pct);
var col=colors[key]||"#5c5c5c";
var csafe=String(col).replace(/[^#0-9a-fA-F]/g,"").slice(0,9)||"#5c5c5c";
var wpx=tok<=0?0:Math.max(2,Math.round(trackW*tok/maxTok));
var mid="—";
if(key==="knowledge"||key==="summary"||key==="pure"||key==="full_recent"){
var cnt=(sg.count!==undefined&&sg.count!==null)?Math.max(0,Math.floor(Number(sg.count)||0)):null;
if(cnt!==null)mid=String(cnt)+"个";
}
if(key==="skill"){
var cnt=(sg.count!==undefined&&sg.count!==null)?Math.max(0,Math.floor(Number(sg.count)||0)):0;
var ac=sg.auto_load_count!==undefined?Math.max(0,Math.floor(Number(sg.auto_load_count)||0)):0;
mid=ac+"/"+cnt;
}
var meta=pct+"% | "+mid+" | "+tok.toLocaleString()+" 令牌";
parts.push(
'<div class="ctx-tip-line">'+
'<span class="ctx-tip-br">'+escapeHtml(lab)+'</span>'+
'<span class="ctx-tip-track">'+
'<span class="ctx-tip-track-fill" style="width:'+wpx+"px;background-color:"+csafe+'"></span>'+
"</span>"+
'<span class="ctx-tip-rest">'+escapeHtml(meta)+"</span>"+
"</div>"
);
}
return parts.join("");
}
function bindCtxLayoutTooltip(barHost, segs, colors){
var tt=ensureCtxLayoutTooltip();
var filtered=[];
for(var i=0;i<segs.length;i++){var s=segs[i];if(!s)continue;filtered.push(s);}
function showTip(){
if(ctxLayoutTipHideTimer){clearTimeout(ctxLayoutTipHideTimer);ctxLayoutTipHideTimer=null;}
if(!filtered.length){if(tt){tt.style.display="none";tt.innerHTML="";}return;}
tt.innerHTML=buildCtxLayoutTooltipHtml(filtered,colors);
tt.style.display="block";
positionCtxLayoutTooltip(tt,barHost);
}
barHost.addEventListener("mouseenter",showTip);
barHost.addEventListener("mouseleave",function(){scheduleHideCtxLayoutTooltip();});
}
function formatCompactTokens(n){var x=Math.max(0,Math.floor(Number(n)||0));if(x>=1e9){var v=x/1e9;return (v>=100?v.toFixed(0):v>=10?v.toFixed(1):v.toFixed(2)).replace(/\.?0+$/,"")+"B";}if(x>=1e6){var v2=x/1e6;return (v2>=100?v2.toFixed(0):v2>=10?v2.toFixed(1):v2.toFixed(2)).replace(/\.?0+$/,"")+"M";}if(x>=1e3){var v3=x/1e3;return (v3>=100?v3.toFixed(0):v3.toFixed(1)).replace(/\.0+$/,"")+"K";}return String(x);}
function estimateCnyFromRates(){if(!pricingRates||pricingRates.ok!==true)return null;var hit=totalCacheHitTokens/1e6*Number(pricingRates.cache_hit_cny_per_m||0);var miss=totalCacheMissTokens/1e6*Number(pricingRates.cache_miss_cny_per_m||0);var out=totalCompletionTokens/1e6*Number(pricingRates.output_cny_per_m||0);return hit+miss+out;}
function costTitleSuffix(){if(!conv)return "—";if(pricingLoading)return "计价查询中…";if(pricingFailed)return "未查到模型计价信息";var x=estimateCnyFromRates();return x===null?"未查到模型计价信息":"￥"+x.toFixed(2);}
async function ensureModelPricing(){var cid=conv,mid=selectedModel;if(!cid||!mid)return;if(pricingCacheKey===cid+"\0"+mid&&(pricingRates!==null||pricingFailed))return;if(pricingInflight)return;pricingInflight=true;pricingLoading=true;queueMicrotask(_updateUsageBottom);var g=++pricingFetchGen;try{var r=await fetch("/api/model-pricing?"+new URLSearchParams({conversation_id:cid,model:mid}));var j=await r.json();if(g!==pricingFetchGen)return;pricingCacheKey=cid+"\0"+mid;if(j&&j.ok===true){pricingRates=j;pricingFailed=false;}else{pricingRates=null;pricingFailed=true;}}catch(e){if(g!==pricingFetchGen)return;pricingCacheKey=cid+"\0"+mid;pricingRates=null;pricingFailed=true;}finally{if(g===pricingFetchGen){pricingLoading=false;pricingInflight=false;queueMicrotask(_updateUsageBottom);}}}
function buildUsageRing(){const million=Math.floor(sessionTokenUsed/SESSION_TOKEN_LIMIT);const p=tokenPercent();const used=Math.max(0,Math.floor(sessionTokenUsed));const ring=document.createElement("span");ring.className="usage-ring";ring.style.setProperty("--p",String(p));const totalCache=totalCacheHitTokens+totalCacheMissTokens;const hitPct=totalCache>0?Math.round(totalCacheHitTokens/totalCache*100):0;var cts=costTitleSuffix();var costD="";if(pricingRates&&pricingRates.ok===true){var _hC=totalCacheHitTokens/1e6*Number(pricingRates.cache_hit_cny_per_m||0);var _mC=totalCacheMissTokens/1e6*Number(pricingRates.cache_miss_cny_per_m||0);var _oC=totalCompletionTokens/1e6*Number(pricingRates.output_cny_per_m||0);var _pm=Number(pricingRates.cache_miss_cny_per_m||0);costD="1m=￥"+_pm.toFixed(2)+" | 总费用: ￥"+(_hC+_mC+_oC).toFixed(2)+" 输入: ￥"+(_hC+_mC).toFixed(2)+" 输出: ￥"+_oC.toFixed(2)+" 缓存命中: ￥"+_hC.toFixed(2)+" 未命中: ￥"+_mC.toFixed(2);}ring.title="已用: "+used.toLocaleString()+"\n输入: "+totalPromptTokens.toLocaleString()+" (显示 "+formatCompactTokens(totalPromptTokens)+")\n输出: "+totalCompletionTokens.toLocaleString()+" (显示 "+formatCompactTokens(totalCompletionTokens)+")\nKV缓存命中: "+totalCacheHitTokens.toLocaleString()+" ("+hitPct+"%)\n未命中: "+totalCacheMissTokens.toLocaleString()+"\n费用预估: "+cts+(costD?"\n"+costD:"");const num=document.createElement("span");num.className="usage-num";num.textContent=million;ring.appendChild(num);return ring;}
function _updateUsageBottom(){
const el=document.getElementById("usage-bottom");if(!el)return;
if(ctxLayoutTooltipEl){ctxLayoutTooltipEl.style.display="none";}
if(ctxLayoutTipHideTimer){clearTimeout(ctxLayoutTipHideTimer);ctxLayoutTipHideTimer=null;}
const ring=buildUsageRing();
const row1=document.createElement("div");row1.className="ub-row ub-row-main";
const title=document.createElement("span");title.className="ub-title";title.textContent="模型用量";
const ringWrap=document.createElement("span");ringWrap.className="ub-ring";ringWrap.appendChild(ring);
const sep=document.createElement("span");sep.className="ub-sep";sep.textContent="|";
const inItem=document.createElement("span");inItem.className="ub-item";inItem.innerHTML='输入: <b>'+formatCompactTokens(totalPromptTokens)+'</b>';
const outItem=document.createElement("span");outItem.className="ub-item";outItem.innerHTML='输出: <b>'+formatCompactTokens(totalCompletionTokens)+'</b>';
const totalCache=totalCacheHitTokens+totalCacheMissTokens;const hitPct=totalCache>0?Math.round(totalCacheHitTokens/totalCache*100):0;const hitItem=document.createElement("span");hitItem.className="ub-item";hitItem.innerHTML='缓存命中: <b>'+formatCompactTokens(totalCacheHitTokens)+' ('+hitPct+'%)</b>';
const missItem=document.createElement("span");missItem.className="ub-item";missItem.innerHTML='未命中: <b>'+formatCompactTokens(totalCacheMissTokens)+'</b>';row1.appendChild(title);row1.appendChild(ringWrap);row1.appendChild(sep);row1.appendChild(inItem);row1.appendChild(outItem);row1.appendChild(hitItem);row1.appendChild(missItem);
var row2=document.createElement("div");row2.className="ub-row ub-row-cost";
var t2=document.createElement("span");t2.className="ub-title";t2.textContent="费用预估";row2.appendChild(t2);
if(pricingRates&&pricingRates.ok===true){var hC=totalCacheHitTokens/1e6*Number(pricingRates.cache_hit_cny_per_m||0);var mC=totalCacheMissTokens/1e6*Number(pricingRates.cache_miss_cny_per_m||0);var oC=totalCompletionTokens/1e6*Number(pricingRates.output_cny_per_m||0);var tC=hC+mC+oC;var pm=Number(pricingRates.cache_miss_cny_per_m||0);
var rI=document.createElement("span");rI.className="ub-item";rI.innerHTML='1m=<b>￥'+pm.toFixed(2)+'</b>';var sMid=document.createElement("span");sMid.className="ub-sep";sMid.textContent=" | ";var tI=document.createElement("span");tI.className="ub-item";tI.innerHTML='总费用: <b>￥'+tC.toFixed(2)+'</b>';var iI=document.createElement("span");iI.className="ub-item";iI.innerHTML='输入: <b>￥'+(hC+mC).toFixed(2)+'</b>';var oI=document.createElement("span");oI.className="ub-item";oI.innerHTML='输出: <b>￥'+oC.toFixed(2)+'</b>';var hI=document.createElement("span");hI.className="ub-item";hI.innerHTML='缓存命中: <b>￥'+hC.toFixed(2)+'</b>';var mI=document.createElement("span");mI.className="ub-item";mI.innerHTML='未命中: <b>￥'+mC.toFixed(2)+'</b>';row2.appendChild(rI);row2.appendChild(sMid);row2.appendChild(tI);row2.appendChild(iI);row2.appendChild(oI);row2.appendChild(hI);row2.appendChild(mI);}else if(pricingLoading){var lI=document.createElement("span");lI.className="ub-item";lI.textContent="计价查询中…";row2.appendChild(lI);}else if(!conv){var dI=document.createElement("span");dI.className="ub-item";dI.textContent="—";row2.appendChild(dI);}else{var eI=document.createElement("span");eI.className="ub-item";eI.textContent="未查到模型计价信息";row2.appendChild(eI);}
var row3=document.createElement("div");row3.className="ub-row ub-row-ctx";
var t3=document.createElement("span");t3.className="ub-title";t3.textContent="上下文视图";
var barHost=document.createElement("div");barHost.className="ctx-bar-host";
var tabCtx=typeof getActiveTab==="function"?getActiveTab():null;
var lay=tabCtx&&tabCtx.lastContextLayout;
var segs=lay&&Array.isArray(lay.segments)?lay.segments:[];
var colors={system:"#a68b32",knowledge:"#0d3a66",summary:"#7a1f1f",skill:"#9c27b0",pure:"#156b4a",full_recent:"#124a3a",mode:"#455a64",remaining:"#4a4e55"};
if(!segs.length){var empty=document.createElement("span");empty.className="ctx-bar-empty";empty.textContent="—";barHost.appendChild(empty);}
else{var bar=document.createElement("div");bar.className="ctx-bar";
for(var si=0;si<segs.length;si++){var sg=segs[si];if(!sg)continue;
var tok=Math.max(0,Math.floor(Number(sg.tokens)||0));
var col=colors[String(sg.key)]||"#5c5c5c";
var colEl=document.createElement("span");colEl.className="ctx-seg-col"+(tok>0?"":" ctx-seg-col-zero");
if(tok>0){colEl.style.flex=String(tok)+" 1 0";colEl.style.minWidth="2px";}else{colEl.style.flex="0 0 2px";colEl.style.minWidth="2px";colEl.style.maxWidth="2px";}
var fill=document.createElement("span");fill.className="ctx-seg ctx-seg-"+String(sg.key||"").replace(/[^a-z0-9_-]/gi,"");fill.style.background=col;fill.style.height="10px";fill.style.borderRadius="2px";fill.style.flexShrink="0";fill.style.width="100%";fill.style.display="block";fill.style.boxSizing="border-box";
colEl.appendChild(fill);bar.appendChild(colEl);}
barHost.appendChild(bar);bindCtxLayoutTooltip(barHost,segs,colors);}
row3.appendChild(t3);row3.appendChild(barHost);
el.innerHTML='';el.appendChild(row1);el.appendChild(row2);el.appendChild(row3);
}

function applyUsageAccumulatorPayload(j){if(!j||typeof j!=="object")return;sessionTokenUsed=Number(j.session_token_used??0)||0;totalPromptTokens=Number(j.total_prompt_tokens??0)||0;totalCompletionTokens=Number(j.total_completion_tokens??0)||0;totalCacheHitTokens=Number(j.total_cache_hit_tokens??0)||0;totalCacheMissTokens=Number(j.total_cache_miss_tokens??0)||0;if(conv)void ensureModelPricing();_updateUsageBottom();}
async function loadUsageAccumulator(){try{const r=await fetch("/api/usage-accumulator");if(!r.ok)return;const j=await r.json();applyUsageAccumulatorPayload(j);}catch(e){}}
async function persistUsageAccumulator(){try{await fetch("/api/usage-accumulator",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_token_used:sessionTokenUsed,total_prompt_tokens:totalPromptTokens,total_completion_tokens:totalCompletionTokens,total_cache_hit_tokens:totalCacheHitTokens,total_cache_miss_tokens:totalCacheMissTokens})});}catch(e){}}
function toolIco(script,args){
const s=(script||"").toLowerCase();
const a=(!args||typeof args!=="object")?{}:args;
if(s.indexOf("read_file")>=0)return "📖";
if(s.indexOf("write_file")>=0)return "📝";
if(s.indexOf("replace_in_file")>=0)return "✏️";
if(s.indexOf("read_write")>=0)return "🔀";
if(s.indexOf("web_fetch")>=0)return "🌐";
if(s.indexOf("file_search")>=0)return "🔎";
if(s.indexOf("ip_geolocate")>=0)return "📍";
if(s.indexOf("open_meteo")>=0)return "🌤️";
if(s.indexOf("image_ocr")>=0)return "🖼";
if(s.indexOf("glob_files")>=0)return "📂";
if(s.indexOf("regex")>=0)return "🔍";
if(s.indexOf("file_ops")>=0)return "📋";
if(s.indexOf("apply_patch")>=0)return "🔧";
if(s.indexOf("git")>=0)return "🌿";
if(s.indexOf("diff")>=0)return "📑";
if(s.indexOf("diagnos")>=0)return "🛠️";
if(s.indexOf("user_confirm")>=0)return "⚠️";
if(s.indexOf("unknown")>=0)return "⚠️";
return "⚙️";}
function toolZh(script){const s=(script||"").toLowerCase();
if(s.indexOf("read_file")>=0)return "读取文件";
if(s.indexOf("write_file")>=0)return "写入文件";
if(s.indexOf("replace_in_file")>=0)return "替换文件内容";
if(s.indexOf("grep_files")>=0)return "搜索文件内容";
if(s.indexOf("find_in_file")>=0)return "定位文件内文本";
if(s.indexOf("read_write")>=0)return "管道读写";
if(s.indexOf("apply_patch")>=0)return "应用补丁";
if(s.indexOf("web_fetch")>=0)return "抓取网页内容";
if(s.indexOf("file_search")>=0)return "搜索文件内容";
if(s.indexOf("ip_geolocate")>=0)return "定位公网地区";
if(s.indexOf("open_meteo")>=0)return "查询天气";
if(s.indexOf("image_ocr")>=0)return "识别图片文字";
if(s.indexOf("glob_files")>=0)return "列出路径";
if(s.indexOf("regex")>=0)return "正则检索与定位";
if(s.indexOf("file_ops")>=0)return "执行文件复制/移动/删除";
if(s.indexOf("git")>=0)return "查看 Git 工作区";
if(s.indexOf("diff")>=0)return "对比文本";
if(s.indexOf("diagnos")>=0)return "统一诊断";
const bn=baseNameOnly(String(script||""));return bn?("执行工具 · "+bn):"执行本地工具";}
function pickFileHint(args){if(!args||typeof args!=="object")return "";
const keys=["file","request_file","payload_file","path","old_path","new_path","source_path","dest_path","source","patch_file"];
for(let k of keys){let v=args[k];if(v===undefined&&args["--"+k]!==undefined)v=args["--"+k];
if(typeof v==="string"&&v){const parts=v.replace(/\\/g,"/").split("/");return parts[parts.length-1]||v;}}
return "";}
function parseToolPayload(args){
if(!args||typeof args!=="object")return null;
let p=args.payload;
if(typeof p==="string"){try{return JSON.parse(p);}catch(e){return null;}}
if(p&&typeof p==="object")return p;
let pj=args["--payload"];
if(typeof pj==="string"){try{return JSON.parse(pj);}catch(e){return null;}}
return null;}
function argGet(args,name){
if(!args||typeof args!=="object")return undefined;
const k=name.indexOf("--")===0?name:"--"+name;
if(args[k]!==undefined)return args[k];
if(args[name]!==undefined)return args[name];
return undefined;}
function baseNameOnly(p){
if(!p||typeof p!=="string")return "";
const parts=p.replace(/\\/g,"/").split("/");
return parts[parts.length-1]||p;}
/** 步骤标题用：优先末两段路径，避免过长 */
function pathLeaf(p){
const s=String(p||"").trim().replace(/\\/g,"/");
if(!s)return "";
const parts=s.split("/").filter(Boolean);
if(parts.length<=2)return parts.join("/")||s;
return parts.slice(-2).join("/");}
function inferLangFromPath(path){
const n=baseNameOnly(String(path||"")).toLowerCase();
const m=n.match(/\.([a-z0-9_+-]+)$/i);
if(!m)return "";
const ext=m[1];
const mp={py:'python',js:'javascript',ts:'typescript',tsx:'typescript',jsx:'javascript',json:'json',yml:'yaml',yaml:'yaml',xml:'xml',html:'html',css:'css',md:'markdown',java:'java',go:'go',rs:'rust',sql:'sql',sh:'bash',bat:'batch',ps1:'powershell',txt:''};
return mp[ext]!==undefined?mp[ext]:"";}
function renderPreviewText(text,lang){
const t=String(text||"");
if(lang)return highlightCode(t,lang);
return escapeHtml(t);
}
function buildToolTitleParts(script,args){
const s=(script+"").toLowerCase();
if(s.indexOf("read_file")>=0){
const path=String(argGet(args,"path")||"").trim();
const leaf=pathLeaf(path)||"（未指定 path）";
const ls=argGet(args,"line_start"), le=argGet(args,"line_end");
const sc=argGet(args,"start_column"), ec=argGet(args,"end_column");
const chs=argGet(args,"char_start"), che=argGet(args,"char_end");
let rng="";
if(ls!=null&&le!=null){
if(sc!=null&&ec!=null)rng=" L"+ls+":"+sc+"–"+le+":"+ec;
else rng=" L"+ls+"–"+le;
}else if(chs!=null||che!=null){
rng=" §"+(chs!=null?chs:0)+"–"+(che!=null?che:"EOF");
}
return {main:"读取文件",fname:leaf+rng};}
if(s.indexOf("write_file")>=0){
const path=String(argGet(args,"path")||"").trim();
const leaf=pathLeaf(path)||"（未指定 path）";
const dr=argGet(args,"dry_run");
const dry=dr!==false&&dr!==0;
const co=!!argGet(args,"create_only");
const tag=dry?"（dry_run 预览）":"（写入）";
const cx=co?" · create_only":"";
return {main:dry?"预览写入":"写入文件",fname:leaf+tag+cx};}
if(s.indexOf("replace_in_file")>=0){
const path=String(argGet(args,"path")||"").trim();
const leaf=pathLeaf(path)||"（未指定 path）";
const rs=argGet(args,"region_start"), re=argGet(args,"region_end");
const ls=argGet(args,"line_start"), le=argGet(args,"line_end");
const sc=argGet(args,"start_column"), ec=argGet(args,"end_column");
const dr=argGet(args,"dry_run");
const dry=dr!==false&&dr!==0;
const rules=argGet(args,"rules");
let mode="字面替换",extra="";
if(rs!=null&&re!=null){mode="区间";extra=" §"+rs+"–"+re;}
else if(ls!=null&&le!=null&&sc!=null&&ec!=null){mode="矩形";extra=" L"+ls+":"+sc+"–"+le+":"+ec;}
else if(Array.isArray(rules)&&rules.length)mode="规则×"+rules.length;
return {main:dry?"预览替换":"替换文件",fname:leaf+" · "+mode+extra+(dry?" · dry_run":"")};}
if(s.indexOf("grep_files")>=0){
const root=String(argGet(args,"path")||"").trim();
const leaf=pathLeaf(root)||root.slice(0,36)+(root.length>36?"…":"");
const pat=String(argGet(args,"pattern")||"").slice(0,40);
const re=!!argGet(args,"regex");
return {main:"搜索"+(re?"（正则）":"（字面）"),fname:leaf+(pat?" · /"+pat+"/":"")};}
if(s.indexOf("find_in_file")>=0){
const path=String(argGet(args,"path")||"").trim();
const leaf=pathLeaf(path)||"（未指定 path）";
const occ=argGet(args,"occurrence");
const re=!!argGet(args,"regex");
const o=occ!=null&&occ!==0?" · 第"+(Number(occ)+1)+"处":"";
return {main:"定位"+(re?"（正则）":"（字面）"),fname:leaf+o};}
if(s.indexOf("read_write")>=0){
const src=pathLeaf(String(argGet(args,"source_path")||""));
const dst=pathLeaf(String(argGet(args,"dest_path")||""));
const ls=argGet(args,"line_start"), le=argGet(args,"line_end");
const chs=argGet(args,"char_start"), che=argGet(args,"char_end");
let rng="";
if(ls!=null&&le!=null)rng=" L"+ls+"–"+le;
else if(chs!=null||che!=null)rng=" §"+(chs!=null?chs:0)+"–"+(che!=null?che:"");
const dr=argGet(args,"dry_run");
const dry=dr!==false&&dr!==0;
return {main:dry?"预览读写管道":"读写管道",fname:(src||"?")+(dst?" → "+dst:"")+rng+(dry?" · dry_run":"")};}
if(s.indexOf("delete_file")>=0){
const leaf=pathLeaf(String(argGet(args,"path")||""))||"（未指定 path）";
const dr=argGet(args,"dry_run");
const dry=dr!==false&&dr!==0;
return {main:dry?"预览删除":"删除文件",fname:leaf+(dry?" · dry_run":"")};}
if(s.indexOf("apply_patch")>=0){
const root=pathLeaf(String(argGet(args,"path")||""))||".";
const dr=argGet(args,"dry_run");
const dry=dr!==false&&dr!==0;
const pf=baseNameOnly(String(argGet(args,"patch_file")||""));
const pt=String(argGet(args,"patch_text")||"");
const hint=pf|| (pt?"内联补丁":"补丁");
return {main:dry?"预览应用补丁":"应用补丁",fname:root+" · "+hint+(dry?" · dry_run":"")};}
if(s.indexOf("run_command")>=0){
const cwd=pathLeaf(String(argGet(args,"cwd")||""));
const cmd=String(argGet(args,"command")||"").replace(/\s+/g," ").trim().slice(0,72);
return {main:"执行命令",fname:(cwd?cwd+" · ":"")+(cmd||"（空命令）")};}
if(s.indexOf("python_inline")>=0){
const cwd=pathLeaf(String(argGet(args,"cwd")||""));
const c0=String(argGet(args,"code")||"");
const c=c0.replace(/\s+/g," ").trim().slice(0,48);
return {main:"执行内联 Python",fname:(cwd?cwd+" · ":"")+(c+(c.length>=48?"…":"")||"（无 code）")};}
if(s.indexOf("archive")>=0){
const act=String(argGet(args,"action")||"").toLowerCase();
const src=pathLeaf(String(argGet(args,"source")||""))||"（未指定 source）";
const dst=pathLeaf(String(argGet(args,"dest")||""));
return {main:"正在"+(act==="list"?"列出":act==="extract"?"解压":"打包")+"压缩包",fname:src+(dst?" · → "+dst:"")};}
if(s.indexOf("data_table")>=0){
const act=String(argGet(args,"action")||"");
const src=pathLeaf(String(argGet(args,"source")||""))||"表格";
const sh=String(argGet(args,"sheet")||"").slice(0,16);
return {main:"处理表格 · "+(act||"?"),fname:src+(sh?" · "+sh:"")};}
if(s.indexOf("todo_list")>=0){
const act=String(argGet(args,"action")||"");
return {main:"Todo-List",fname:act||"query"};}
if(s.indexOf("user_confirm")>=0){
const t=String(argGet(args,"title")||"").slice(0,40);
return {main:"等待用户确认",fname:t||"（无标题）"};}
if(s.indexOf("run_type")>=0){
const rt=String(argGet(args,"run_type")||"").trim().toLowerCase();
return {main:rt?"切换运行模式":"查询运行模式",fname:rt||"auto/plan/execute"};}
if(s.indexOf("glob_files")>=0){
const root=String(argGet(args,"path")||"");
const glob=String(argGet(args,"glob_pattern")||"*");
const leaf=pathLeaf(root)||(root.length>28?root.slice(0,28)+"…":root);
const rec=argGet(args,"recursive");
const et=String(argGet(args,"entry_type")||"").toLowerCase();
const scope=(et==="dir"?"仅目录":et==="all"?"全部":"")+(et==="file"||!et?"文件":"");
return {main:"列出路径"+(scope?"（"+scope+"）":""),fname:leaf+(glob&&glob!=="*"&&glob!=="**/*"?" · "+glob:"")+(rec===false?"":" · 递归")};}
if(s.indexOf("web_fetch")>=0){
const url=String(argGet(args,"url")||"");
try{const u=new URL(url);const path=u.pathname==="/"?"":u.pathname;return {main:"抓取 "+u.hostname+(path?path.length>36?path.slice(0,36)+"…":path:""),fname:""};}catch(e){return {main:url?"抓取 "+url.slice(0,56):"抓取网页",fname:""};}}
if(s.indexOf("file_search")>=0){
const pat=String(argGet(args,"pattern")||"").slice(0,40);
const root=String(argGet(args,"path")||"").trim();
const leaf=pathLeaf(root)||(root.length>28?root.slice(0,28)+"…":root);
return {main:"全文搜索"+(argGet(args,"regex")?"（正则）":"（字面）"),fname:leaf+(pat?" · /"+pat+"/":"")};}
if(s.indexOf("ip_geolocate")>=0){
const ip=String(argGet(args,"ip")||"").trim();
return {main:"定位公网地区"+(ip?" · "+ip:""),fname:""};}
if(s.indexOf("open_meteo")>=0){
const loc=String(argGet(args,"location")||"").slice(0,20);
const la=String(argGet(args,"latitude")||"").trim();
const lo=String(argGet(args,"longitude")||"").trim();
if(la&&lo)return {main:"查询天气 · "+la+","+lo,fname:""};
return {main:"查询天气"+(loc?" · "+loc:""),fname:""};}
if(s.indexOf("regex_locate")>=0){
const pat=String(argGet(args,"pattern")||"").slice(0,48);
const tgt=pathLeaf(String(argGet(args,"path")||""));
const glob=String(argGet(args,"glob_pattern")||"");
return {main:"正则检索",fname:(tgt||"?")+(pat?" · /"+pat+"/":"")+(glob&&glob!=="*"?" · "+glob:"")};}
if(s.indexOf("file_ops")>=0){
const act=String(argGet(args,"action")||"").toLowerCase();
const src=baseNameOnly(String(argGet(args,"source")||""));
const dst=baseNameOnly(String(argGet(args,"dest")||""));
if(act==="delete")return {main:"移入回收站 · "+(src||"见参数"),fname:""};
if(act==="copy")return {main:"复制 · "+src+(dst?" → "+dst:""),fname:""};
if(act==="move")return {main:"移动 · "+src+(dst?" → "+dst:""),fname:""};
if(act==="rename")return {main:"重命名 · "+src+(dst?" → "+dst:""),fname:""};
return {main:(src?"文件操作 · "+src:"文件操作"),fname:""};}
if(s.indexOf("text_diff")>=0){
const lf=baseNameOnly(String(argGet(args,"left_file")||""));
const rf=baseNameOnly(String(argGet(args,"right_file")||""));
if(lf||rf)return {main:"对比 · "+(lf||"?")+" ↔ "+(rf||"?"),fname:""};
return {main:"对比文本",fname:""};}
if(s.indexOf("git_workspace")>=0){
const root=pathLeaf(String(argGet(args,"path")||""))||".";
const mode=String(argGet(args,"mode")||"worktree");
return {main:"查看 Git",fname:root+" · "+mode};}
if(s.indexOf("image_ocr")>=0){
const src=pathLeaf(String(argGet(args,"source")||""))||"（未指定 source）";
const eng=String(argGet(args,"engine")||"auto").toLowerCase();
const reg=String(argGet(args,"region")||"").trim();
const extra=(eng&&eng!=="auto")?" · "+eng:"";
const rg=reg?" · 区域 "+reg:"";
return {main:"识别图片文字",fname:src+extra+rg};}
if(s.indexOf("env_probe")>=0)return {main:"探测运行环境",fname:""};
if(s.indexOf("diagnos")>=0){
const root=pathLeaf(String(argGet(args,"path")||""))||".";
return {main:"统一诊断",fname:root};}
const unk='(unknown)';
if(s===unk)return {main:"无法识别的工具（见下方参数）",fname:""};
return {main:toolZh(script),fname:""};}
function linesDiff(oldT,newT){
const A=String(oldT??"").split("\n");
const B=String(newT??"").split("\n");
const out=[];
let i=0,j=0;
while(i<A.length||j<B.length){
if(i<A.length&&j<B.length&&A[i]===B[j]){out.push({t:" ",l:A[i]});i++;j++;}
else if(j<B.length&&(i>=A.length||A[i]!==B[j])){out.push({t:"+",l:B[j]});j++;}
else if(i<A.length){out.push({t:"-",l:A[i]});i++;}
else{out.push({t:"+",l:B[j]});j++;}
}
return out;}
function renderDiffRows(container,oldT,newT){
container.innerHTML="";
for(const r of linesDiff(oldT,newT)){
const d=document.createElement("div");
if(r.t==="-")d.className="d-del";
else if(r.t==="+")d.className="d-add";
else d.className="d-eq";
d.innerHTML=diffRowInnerHtml(r);
container.appendChild(d);
}}
function unifiedDiffRowClass(raw){
var s=String(raw||"");
if(s.startsWith("---"))return "d-meta";
if(s.startsWith("+++"))return "d-meta";
if(s.indexOf("@@")===0)return "d-meta";
if(s.startsWith("+"))return "d-add";
if(s.startsWith("-"))return "d-del";
return "d-eq";}
function renderUnifiedDiffRows(container,lines){
container.innerHTML="";
if(!Array.isArray(lines))return;
for(var i=0;i<lines.length;i++){
var L=lines[i]==null?"":String(lines[i]);var cls=unifiedDiffRowClass(L);
var el=document.createElement("div");
el.className=cls;
if(cls==="d-meta"){el.textContent=L;}
else if(L.charAt(0)==="-"){el.innerHTML=diffRowInnerHtml({t:"-",l:L.replace(/^-\s?/,"")});}
else if(L.charAt(0)==="+"){el.innerHTML=diffRowInnerHtml({t:"+",l:L.replace(/^\+\s?/,"")});}
else{el.innerHTML=diffRowInnerHtml({t:" ",l:L.replace(/^\s/,"")});}
container.appendChild(el);}}
// 全局滚动代理 — document 捕获阶段统一处理所有 scroll，无需对元素逐个注册
(function(){
  document.addEventListener("scroll",function(e){
    var el=e.target;
    if(!el||el.nodeType!==1)return;
    // 程序化滚动（scrollToBottom发起的）→ 不设免疫期
    if(el._ascrollPG){el._ascrollPG=false;return;}
    // 用户手动滚动 → 刷新免疫期，同时根据当前位置设跟随开关
    el._ascrollTM=Date.now();
    if(el.scrollHeight-el.scrollTop-el.clientHeight>60){
      el._ascrollStop=true;
    }else{
      el._ascrollStop=false;
    }
  },true);
})();

function scrollToBottom(el,force){
  if(!el)return;
  // force：初始化强制滚动，跳过所有检查
  if(force){
    el.scrollTop=el.scrollHeight;
    return;
  }
  // 200ms免疫期：避免与用户主动滚动竞争控制权
  if(el._ascrollTM&&Date.now()-el._ascrollTM<200)return;
  // _ascrollStop===true表示已取消跟随；false/undefined均表示跟随
  if(el._ascrollStop===true)return;
  // 设程序化滚动标记，scroll事件处理器跳过免疫期
  el._ascrollPG=true;
  el.scrollTop=el.scrollHeight;
}
function isNearBottom(el,threshold=60){
  if(!el) return true;
  return el.scrollHeight-el.scrollTop-el.clientHeight<threshold;
}
function scrollMsgsToBottom(){scrollToBottom(msgs);}
function scrollMsgsToBottomAfterLayout(){scrollToBottomAfterLayout(msgs,true);}
function calcDiffStats(oldT,newT){var A=String(oldT??'').split('\n'),B=String(newT??'').split('\n');var a=0,d=0,i=0,j=0;while(i<A.length||j<B.length){if(i<A.length&&j<B.length&&A[i]===B[j]){i++;j++;}else if(j<B.length&&(i>=A.length||A[i]!==B[j])){a++;j++;}else if(i<A.length){d++;i++;}else{a++;j++;}}return {add:a,del:d};}
function diffLabel(s){return(s.del>0?' <span style="color:#f48771">-'+s.del+'</span>':'')+(s.add>0?' <span style="color:#89d185">+'+s.add+'</span>':'');}
function diffLabelPlain(s){return(s.del>0?' -'+s.del:'')+(s.add>0?' +'+s.add:'');}

function fillToolPreview(o,ev){
const sc=(ev.script||"").toLowerCase();
if(!o.previewSlot)return;
if(sc.indexOf("run_command")>=0){
if(window.DEBUG)console.log("fillToolPreview: run_command entered, o=",o," ev.preview=",ev.preview);
o.previewSlot.innerHTML="";
o.previewSlot.style.display="none";
try{
var _raw=ev.preview||"{}";
if(window.DEBUG)console.log("fillToolPreview: _raw=",_raw);
const j=JSON.parse(_raw);
if(window.DEBUG)console.log("fillToolPreview: parsed=",j);
const out=(j&&j.data&&typeof j.data.stdout==="string")?j.data.stdout:"";
if(window.DEBUG)console.log("fillToolPreview: out=",out);
if(out){
o.previewSlot.style.display="block";
const cap=document.createElement("p");cap.textContent="stdout 预览";
const pr=document.createElement("pre");pr.className="sub";pr.innerHTML=out.length>12000?highlightCode(out.slice(0,12000),'bash')+"\n…":highlightCode(out,'bash');
o.previewSlot.appendChild(cap);o.previewSlot.appendChild(pr);
}
// 强制在对话区添加卡片
var testContent=out||"(空输出, raw="+_raw.slice(0,200)+")";
var cmdName=(o.args||{}).command||"命令行";
if(typeof cmdName==="string"&&cmdName.trim())cmdName=cmdName.trim().slice(0,60);
var cardHtml='<div class="chat-diff-card"><div class="chat-diff-cap">'+escapeHtml(cmdName)+'</div><div class="diff-unified diff-surface-adaptive"><pre style="margin:0;padding:4px 8px;font-size:11px">'+escapeHtml(testContent)+'</pre></div></div>';
var chatDiv=document.createElement("div");chatDiv.className="b a";chatDiv.innerHTML=cardHtml;
//
}catch(e){
if(window.DEBUG)console.error("fillToolPreview: error",e);
var errCard='<div class="chat-diff-card"><div class="chat-diff-cap">❌ 渲染异常</div><div class="diff-unified diff-surface-adaptive"><pre style="margin:0;padding:4px 8px;font-size:11px">'+escapeHtml(e.message)+'</pre></div></div>';
var errDiv=document.createElement("div");errDiv.className="b a";errDiv.innerHTML=errCard;
}
return;}
if(sc.indexOf("regex_locate")>=0){
o.previewSlot.innerHTML="";
o.previewSlot.style.display="none";
try{
var _rl=JSON.parse(ev.preview||"{}");
var _rlData=_rl&&_rl.data;
if(_rlData&&_rlData.type==="regex_locate"&&Array.isArray(_rlData.snippets)&&_rlData.snippets.length){
o.previewSlot.style.display="block";
var _rlCap=document.createElement("p");_rlCap.textContent="匹配结果 "+_rlData.count+" 处（预览 "+_rlData.snippets.length+" 条）";
var _rlPre=document.createElement("pre");_rlPre.className="sub";
_rlPre.innerHTML=_rlData.snippets.map(function(s){return escapeHtml(s);}).join("\n");
o.previewSlot.appendChild(_rlCap);o.previewSlot.appendChild(_rlPre);
// 推送到对话区：卡片插在流式气泡下方，气泡移到卡片后继续流式
var _rlCardHtml='<div class="chat-diff-card"><div class="chat-diff-cap">快速查找 · 共 '+_rlData.count+' 处</div><div class="diff-unified diff-surface-adaptive"><pre style="margin:0;padding:4px 8px;font-size:11px;line-height:1.4">'+escapeHtml(_rlData.snippets.join("\n"))+'</pre></div></div>';
var _rlDiv=document.createElement("div");_rlDiv.className="b a";_rlDiv.innerHTML=_rlCardHtml;
if(streamAssistantEl){streamAssistantEl.after(_rlDiv);streamAssistantEl=null;streamAssistantText="";}else{msgs.appendChild(_rlDiv);}
scrollMsgsToBottom();
}
}catch(e){if(window.DEBUG)console.error("regex_locate preview error",e);}
return;}
if(sc.indexOf("text_diff")>=0){
o.previewSlot.innerHTML="";
o.previewSlot.style.display="none";
try{
const _dj=JSON.parse(ev.preview||"{}");
const arr=(_dj&&_dj.data&&Array.isArray(_dj.data.diff))?_dj.data.diff:null;
if(arr&&arr.length){o.previewSlot.style.display="block";
const _cap=document.createElement("p");_cap.textContent="unified diff 预览";
const _box=document.createElement("div");_box.className="diff-unified diff-surface-adaptive";
renderUnifiedDiffRows(_box,arr);
o.previewSlot.appendChild(_cap);o.previewSlot.appendChild(_box);}
}catch(_e){}
return;}
if(sc.indexOf("replace_in_file")>=0){
o.previewSlot.innerHTML="";
o.previewSlot.style.display="none";
try{
const j=JSON.parse(ev.preview||"{}");
const dt=(j&&j.data&&typeof j.data.diffText==="string")?j.data.diffText:"";
const fp=(j&&j.data&&j.data.path)?String(j.data.path):"";
if(dt&&dt.trim()){
o.previewSlot.style.display="block";
const cap=document.createElement("p");
cap.textContent=(fp?baseNameOnly(fp)+" · ":"")+"替换 diff 预览";
const wrap=document.createElement("div");
wrap.innerHTML=renderUnifiedDiffBodyAsCardsHtml(dt);
if(!wrap.innerHTML.trim()){
const box=document.createElement("div");
box.className="diff-unified diff-surface-adaptive";
renderUnifiedDiffRows(box,dt.split(/\r?\n/));
wrap.appendChild(box);
}
o.previewSlot.appendChild(cap);
o.previewSlot.appendChild(wrap);
}
}catch(_e2){}
return;}
}
function analysisFollowupPhrase(script,args){
const s=(script+"").toLowerCase();
if(s.indexOf("web_fetch")>=0)return "抓取内容";
if(s.indexOf("file_search")>=0)return "搜索结果";
if(s.indexOf("grep_files")>=0)return "检索结果";
if(s.indexOf("image_ocr")>=0)return "识别结果";
if(s.indexOf("ip_geolocate")>=0)return "地理定位";
if(s.indexOf("open_meteo")>=0)return "天气数据";
if(s.indexOf("replace_in_file")>=0)return "替换预览";
if(s.indexOf("glob_files")>=0)return "目录结果";
if(s.indexOf("regex_locate")>=0)return "检索结果";
if(s.indexOf("file_ops")>=0)return "文件操作结果";
if(s.indexOf("apply_patch")>=0)return "补丁结果";
if(s.indexOf("text_diff")>=0)return "对比结果";
if(s.indexOf("git_workspace")>=0)return "工作区状态";
if(s.indexOf("diagnos")>=0)return "诊断结果";
if(s.indexOf("run_command")>=0)return "命令输出";
if(s.indexOf("python_inline")>=0)return "内联代码输出";
return "工具输出";}
function clearLlmAnim(){
if(lastLlm&&lastLlm.dotsTimer){clearInterval(lastLlm.dotsTimer);lastLlm.dotsTimer=null;}}
function promoteReasoningToChatIfNeeded(){
var rt=(lastLlm&&lastLlm.reasoningText||"").trim();
if(!rt)return;
if((streamAssistantText||"").trim())return;
appendAssistantDelta(rt);}
function appendReasoningDelta(ev){
var d=String(ev.delta||"");if(!d)return;if(!lastLlm)return;if(ev.round!=null&&lastLlm.round!=null&&ev.round!==lastLlm.round)return;
lastLlm.reasoningText=(lastLlm.reasoningText||"")+d;if(lastLlm.thLb&&lastLlm.thPb){lastLlm.thLb.style.display="block";lastLlm.thPb.style.display="block";lastLlm.thPb.textContent=lastLlm.reasoningText;scrollToBottom(lastLlm.thPb);}}
function applyReasoningSync(ev){
var t=String(ev.text||"");if(!lastLlm)return;if(ev.round!=null&&lastLlm.round!=null&&ev.round!==lastLlm.round)return;
lastLlm.reasoningText=t;if(lastLlm.thLb&&lastLlm.thPb){lastLlm.thLb.style.display="block";lastLlm.thPb.style.display="block";lastLlm.thPb.textContent=lastLlm.reasoningText;scrollToBottom(lastLlm.thPb);}}
function finishLlmTitle(ok){
if(!lastLlm||!lastLlm.msgSpan)return;
if(lastLlm.dotsTimer){clearInterval(lastLlm.dotsTimer);lastLlm.dotsTimer=null;}
lastLlm.dotsSpan.textContent="";
if(ok){lastLlm.msgSpan.textContent="思考完毕";}
else{lastLlm.msgSpan.textContent="思考异常";}}
function addLlmRound(r){
hideChatLoading();
stepSeq++;
const n=r||1;
llmStreamBuffer.round=n;llmStreamBuffer.reqHtml="";llmStreamBuffer.resHtml="";llmStreamBuffer.consumed=false;
if(n>1&&streamAssistantEl){pendingDeltaSeparator=true;}
let baseMsg="正在思考中";
if(n>1){const tail=lastAnalysisTail||"工具输出";baseMsg="分析"+tail;lastAnalysisTail="";}
const c=document.createElement("div");c.className="step card";
const h=document.createElement("div");h.className="ch ch-toggle";h.setAttribute("role","button");
const left=document.createElement("div");left.className="tit tit-row";
const ic=document.createElement("span");ic.className="step-ico";ic.textContent="🧠";ic.title="思考推理";
const sn=document.createElement("span");sn.className="step-num";sn.textContent="步骤 "+stepSeq+"：";
const msgSpan=document.createElement("span");msgSpan.className="step-llm-msg";msgSpan.textContent=baseMsg;
const dotsSpan=document.createElement("span");dotsSpan.className="step-dots";dotsSpan.textContent="";
left.appendChild(ic);left.appendChild(sn);left.appendChild(msgSpan);left.appendChild(dotsSpan);
const tag=document.createElement("span");tag.className="tag tag-run";tag.textContent="进行中";
h.appendChild(left);h.appendChild(tag);
const body=document.createElement("div");body.className="card-body";
const inner=document.createElement("div");inner.className="card-body-inner";
const thLb=document.createElement("p");thLb.className="lbl reasoning-section-lbl";thLb.textContent="思考过程";thLb.style.display="none";
const thPb=document.createElement("pre");thPb.className="sub reasoning-pre";thPb.style.display="none";thPb.textContent="";
const rqLb=document.createElement("p");rqLb.className="lbl";rqLb.textContent="请求";rqLb.style.display="none";
const rqPb=document.createElement("pre");rqPb.className="sub";rqPb.style.display="none";rqPb.textContent="";
const rsLb=document.createElement("p");rsLb.className="lbl";rsLb.textContent="响应";rsLb.style.display="none";
const rsPb=document.createElement("pre");rsPb.className="sub";rsPb.style.display="none";rsPb.textContent="";
inner.appendChild(thLb);inner.appendChild(thPb);inner.appendChild(rqLb);inner.appendChild(rqPb);inner.appendChild(rsLb);inner.appendChild(rsPb);body.appendChild(inner);
c.appendChild(h);c.appendChild(body);
h.onclick=function(){c.classList.toggle("open");};
appendStep(c);
let d=0;
const timer=setInterval(function(){d=(d+1)%4;dotsSpan.textContent=".".repeat(d);},400);
lastLlm={tag:tag,msgSpan:msgSpan,dotsSpan:dotsSpan,dotsTimer:timer,round:n,thLb:thLb,thPb:thPb,reasoningText:"",reqLb:rqLb,reqPb:rqPb,resLb:rsLb,resPb:rsPb,cardEl:c,stepNum:stepSeq};
}
function onLlmRequest(ev){
if(!lastLlm)return;
if(ev&&ev.round!=null&&lastLlm.round!=null&&ev.round!==lastLlm.round)return;
var j=JSON.stringify(ev.params||{},null,2);
var h=highlightJson(j);
llmStreamBuffer.reqHtml=h;
if(lastLlm.reqLb&&lastLlm.reqPb){lastLlm.reqLb.style.display="block";lastLlm.reqPb.style.display="block";lastLlm.reqPb.textContent=j;
llmStreamBuffer.reqHtml=j;}
}
function onLlmResponse(ev){
if(!lastLlm)return;
if(ev&&ev.round!=null&&lastLlm.round!=null&&ev.round!==lastLlm.round)return;
var j=JSON.stringify(ev.params||{},null,2);
var h=highlightJson(j);
llmStreamBuffer.resHtml=j;
if(lastLlm.resLb&&lastLlm.resPb){lastLlm.resLb.style.display="block";lastLlm.resPb.style.display="block";lastLlm.resPb.textContent=j;}
}
function createToolAnalysisBlock(){
var wrap=document.createElement("div");
wrap.className="llm-merge-block";
var rqLb=document.createElement("p");rqLb.className="lbl";rqLb.textContent="分析的请求";rqLb.style.display="none";
var rqPb=document.createElement("pre");rqPb.className="sub";rqPb.style.display="none";rqPb.textContent="";
var rsLb=document.createElement("p");rsLb.className="lbl";rsLb.textContent="分析的响应";rsLb.style.display="none";
var rsPb=document.createElement("pre");rsPb.className="sub";rsPb.style.display="none";rsPb.textContent="";
wrap.appendChild(rqLb);wrap.appendChild(rqPb);wrap.appendChild(rsLb);wrap.appendChild(rsPb);
return wrap;
}
function fillAnalysisBlockFromBuffer(wrap){
if(!wrap)return;
var rqLb=wrap.children[0],rqPb=wrap.children[1],rsLb=wrap.children[2],rsPb=wrap.children[3];
if(llmStreamBuffer.reqHtml&&rqLb&&rqPb){rqLb.style.display="block";rqPb.style.display="block";rqPb.textContent=llmStreamBuffer.reqHtml;}
if(llmStreamBuffer.resHtml&&rsLb&&rsPb){rsLb.style.display="block";rsPb.style.display="block";rsPb.textContent=llmStreamBuffer.resHtml;}
}
function removeMergedLlmCard(){
if(!lastLlm||!lastLlm.cardEl)return;
clearLlmAnim();
var el=lastLlm.cardEl;
var ix=pendingStepEls.indexOf(el);
if(ix>=0)pendingStepEls.splice(ix,1);
else if(el.parentNode)el.parentNode.removeChild(el);
try{el.remove();}catch(e){}
lastLlm=null;
}
function flushPendingToolTags(){
for(var i=0;i<pendingToolTags.length;i++){
var o=pendingToolTags[i];
if(o&&o.tag){
o.tag.textContent=o.ok?"Done":"Fail";
o.tag.className="tag "+(o.ok?"ok":"bad");
}
}
pendingToolTags=[];
}
function abortPendingToolTags(){
for(var j=0;j<pendingToolTags.length;j++){
var p=pendingToolTags[j];
if(p&&p.tag){p.tag.innerHTML="";p.tag.textContent="Fail";p.tag.className="tag bad";}
}
pendingToolTags=[];
}
function stopPendingToolTags(){
for(var j=0;j<pendingToolTags.length;j++){
var p=pendingToolTags[j];
if(p&&p.tag){p.tag.innerHTML="";p.tag.textContent="Stoped";p.tag.className="tag stoped";}
}
pendingToolTags=[];
}
function stopOpenToolTags(){
try{toolOpen.forEach(function(o){if(!o)return;if(o.progressBadge&&o.progressBadge.parentNode)o.progressBadge.parentNode.removeChild(o.progressBadge);if(o.fileSpan&&o.fileSpan.parentNode)o.fileSpan.parentNode.removeChild(o.fileSpan);if(o.spinWrap&&o.spinWrap.parentNode)o.spinWrap.parentNode.removeChild(o.spinWrap);if(o.tag){o.tag.innerHTML="";o.tag.textContent="Stoped";o.tag.className="tag stoped";}});}catch(e){}
toolOpen.clear();
}
function markCurrentTurnStoped(){
stopPendingToolTags();stopOpenToolTags();if(lastLlm&&lastLlm.tag){finishLlmTitle(false);lastLlm.tag.innerHTML="";lastLlm.tag.textContent="Stoped";lastLlm.tag.className="tag stoped";lastLlm=null;}pendingStepEls=[];
}
function isHostProgressToolScript(script){
var s=String(script||"").toLowerCase();
return s.indexOf("file_search")>=0||s.indexOf("grep_files")>=0||s.indexOf("regex_locate")>=0||s.indexOf("run_command")>=0||s.indexOf("python_inline")>=0;
}
function isStreamOutputToolScript(script){
var s=String(script||"").toLowerCase();
return s.indexOf("run_command")>=0||s.indexOf("python_inline")>=0;
}
function isRunCommandToolScript(script){
return String(script||"").toLowerCase().indexOf("run_command")>=0;
}
async function submitCommandInputToServer(cid,tid,input){
try{
var r=await fetch("/api/chat/command-input",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:cid,tool_call_id:tid,input:input})});
return r.ok;
}catch(e){return false;}
}
function ensureRunCommandLivePanel(o){
if(!o||o.liveOutPre)return;
if(!o.resPb)return;
o.resLb.style.display="block";
o.resPb.style.display="block";
o.resLb.textContent="实时输出";
o.resPb.className="sub chat-run-live-out";
o.liveOutPre=o.resPb;
}
// 在左侧对话区创建/获取“执行命令脚本 + 执行结果”卡片；执行结果区随 stdout 增量刷新
function ensureChatRunCard(o){
if(!o)return null;
if(o.chatRunCard&&o.chatRunCard.parentNode)return o.chatRunCard;
var _cardDiv=document.createElement("div");_cardDiv.className="b a";
var _cardTitle=(o.stepTitle&&o.stepTitle.trim())?escapeHtml(o.stepTitle.trim()):"执行命令脚本";
var _cmdRaw=(o.args&&(o.args.command||o.args.code))||"";
var _cmdLang=detectScriptLang(_cmdRaw,(o.args&&(o.args.language||o.args.lang))||"");
var _cmdCls=hljsCodeClass(_cmdLang);
_cardDiv.innerHTML='<div class="chat-diff-card"><div class="chat-diff-cap">'+_cardTitle+'<span class="chat-run-status" style="margin-left:8px;font-size:12px;font-weight:normal;color:#e0a300"></span></div><div class="diff-unified chat-run-wrap chat-run-cmd-block"><pre class="chat-run-pre chat-run-pre--cmd"><code'+(_cmdCls?' class="'+_cmdCls+'"':'')+'>'+highlightCode(_cmdRaw,_cmdLang)+'</code></pre></div><div class="chat-diff-cap chat-diff-cap--sect">执行结果</div><pre class="chat-run-pre chat-run-pre--out"><code></code></pre></div>';
if(streamAssistantEl){streamAssistantEl.after(_cardDiv);streamAssistantEl=null;streamAssistantText="";}else{msgs.appendChild(_cardDiv);}
o.chatRunCard=_cardDiv;
o.chatRunOutPre=_cardDiv.querySelector(".chat-run-pre--out code");
o.chatRunStatus=_cardDiv.querySelector(".chat-run-status");
startChatRunCountdown(o);
scrollMsgsToBottom();
return _cardDiv;
}
// 标题后状态注记 + 基于 timeout_sec 的倒计时（等待执行结果中…剩余 Ns）
function startChatRunCountdown(o){
if(!o||o._chatRunTimer)return;
var _to=0;try{_to=parseInt((o.args&&o.args.timeout_sec)||0,10)||0;}catch(e){_to=0;}
var _start=Date.now();
function _tick(){
if(!o.chatRunStatus||!o.chatRunCard||!o.chatRunCard.parentNode){stopChatRunCountdown(o);return;}
var _elapsed=Math.floor((Date.now()-_start)/1000);
if(_to>0){
var _left=_to-_elapsed;
if(_left<0)_left=0;
o.chatRunStatus.textContent="⏳ 等待执行结果中…（剩余 "+_left+"s / 超时 "+_to+"s）";
}else{
o.chatRunStatus.textContent="⏳ 等待执行结果中…（已用时 "+_elapsed+"s）";
}
}
_tick();
o._chatRunTimer=setInterval(_tick,1000);
}
function stopChatRunCountdown(o){
if(o&&o._chatRunTimer){try{clearInterval(o._chatRunTimer);}catch(e){}o._chatRunTimer=null;}
}
// 增量刷新对话区卡片的执行结果文本
function updateChatRunCardOutput(o,text){
if(!o)return;
if(!o.chatRunCard||!o.chatRunCard.parentNode)ensureChatRunCard(o);
if(o.chatRunOutPre){o.chatRunOutPre.textContent=text!=null?String(text):"";scrollMsgsToBottom();}
}
// 命令结束：停止倒计时，标题状态置为完成/失败
function finalizeChatRunCard(o,ok){
if(!o)return;
stopChatRunCountdown(o);
if(o.chatRunStatus){o.chatRunStatus.textContent=ok?"✓ 执行完成":"✗ 执行结束（失败）";o.chatRunStatus.style.color=ok?"#3fb950":"#f85149";}
}
function renderRunCommandInputBar(o,ev){
var card=findStepCardForToolCall(String(ev.tool_call_id||"").trim());
if(!card||o.cmdInputBar)return;
var ai=ev.awaiting_input||{};
var title=String(ai.title||"命令需要确认").trim();
var opts=Array.isArray(ai.options)&&ai.options.length?ai.options:["Y","N"];
var bar=document.createElement("div");
bar.className="cmd-input-bar";
bar.style.cssText="margin:8px 0;padding:8px;background:#1a1d24;border:1px solid #333;border-radius:6px;font-size:12px";
var cap=document.createElement("div");
cap.style.cssText="color:#ccc;margin-bottom:6px;white-space:pre-wrap;max-height:120px;overflow:auto";
cap.textContent=title;
bar.appendChild(cap);
var row=document.createElement("div");
row.style.cssText="display:flex;flex-wrap:wrap;gap:8px;align-items:center";
opts.forEach(function(opt){
var btn=document.createElement("button");
btn.type="button";
btn.textContent=opt;
btn.style.cssText="padding:4px 12px;cursor:pointer";
btn.onclick=function(){
var cid=normalizeConversationId(ev.conversation_id||activeConversationId);
var tid=String(ev.tool_call_id||"").trim();
submitCommandInputToServer(cid,tid,opt);
if(o.tag){o.tag.textContent="已发送 "+opt;o.tag.className="tag tag-run";}
try{bar.remove();}catch(e){}o.cmdInputBar=null;
};
row.appendChild(btn);
});
var hint=document.createElement("span");
hint.style.cssText="color:#888;margin-left:4px";
hint.textContent="（也可在下方输入框自定义后发送）";
row.appendChild(hint);
var custom=document.createElement("input");
custom.type="text";
custom.placeholder="自定义输入，如 Y";
custom.style.cssText="flex:1;min-width:120px;padding:4px 8px;background:#0d1117;color:#eee;border:1px solid #444;border-radius:4px";
var sendBtn=document.createElement("button");
sendBtn.type="button";
sendBtn.textContent="发送";
sendBtn.onclick=function(){
var v=String(custom.value||"").trim();
if(!v)return;
var cid=normalizeConversationId(ev.conversation_id||activeConversationId);
var tid=String(ev.tool_call_id||"").trim();
submitCommandInputToServer(cid,tid,v);
try{bar.remove();}catch(e){}o.cmdInputBar=null;
};
row.appendChild(custom);
row.appendChild(sendBtn);
bar.appendChild(row);
var body=card.querySelector(".card-body-inner")||card.querySelector(".card-body");
if(body)body.appendChild(bar);
else card.appendChild(bar);
o.cmdInputBar=bar;
}
function onToolStart(ev){if(!anyToolThisTurn){anyToolThisTurn=true;}
var merged=!!(lastLlm&&lastLlm.cardEl&&!llmStreamBuffer.consumed);
var mergeKeepOpen=merged&&lastLlm.cardEl?lastLlm.cardEl.classList.contains("open"):false;
var dispStep;
if(merged){dispStep=lastLlm.stepNum!=null?lastLlm.stepNum:stepSeq;}
else{stepSeq++;dispStep=stepSeq;}
if(!merged){flushPendingSteps();}
const tid=ev.tool_call_id!=null&&String(ev.tool_call_id).trim()!==""?String(ev.tool_call_id).trim():String(Math.random());
const tp=buildToolTitleParts(ev.script,ev.args||{});
const _stt=String(ev.step_title||"").trim();
if(_stt){
const _det=String(tp.fname||"").trim();
tp.main=_stt;
tp.fname=_det;}
const c=document.createElement("div");c.className="step card";c.setAttribute("data-tool-call-id", tid);
const h=document.createElement("div");h.className="ch ch-toggle";h.setAttribute("role","button");
const left=document.createElement("div");left.className="tit tit-row";
const ic=document.createElement("span");ic.className="step-ico";ic.textContent=toolIco(ev.script,ev.args||{});
const num=document.createElement("span");num.className="step-num";num.textContent="步骤 "+dispStep+"：";
const tx=document.createElement("span");tx.className="step-title-wrap";
const txM=document.createElement("span");txM.textContent=tp.main;tx.appendChild(txM);
if(tp.fname){const txDot=document.createElement("span");txDot.textContent=" · ";tx.appendChild(txDot);const txF=document.createElement("span");txF.className="step-fname";txF.textContent=tp.fname;tx.appendChild(txF);}
let spinWrap=null;
if((ev.script||"").toLowerCase().indexOf("web_fetch")>=0){spinWrap=document.createElement("span");spinWrap.className="step-inline-spin";spinWrap.innerHTML="<span class=\"step-spinner\" title=\"加载中\"></span>";tx.appendChild(spinWrap);}
if(isHostProgressToolScript(ev.script)){spinWrap=document.createElement("span");spinWrap.className="step-inline-spin";spinWrap.innerHTML="<span class=\"step-spinner\" title=\"加载中\"></span>";tx.appendChild(spinWrap);}
left.appendChild(ic);left.appendChild(num);left.appendChild(tx);
const tag=document.createElement("span");tag.className="tag tag-run";tag.textContent="运行中";
h.appendChild(left);h.appendChild(tag);
const body=document.createElement("div");body.className="card-body";
const inner=document.createElement("div");inner.className="card-body-inner";
const la=document.createElement("p");la.className="lbl";la.textContent="请求";
const pa=document.createElement("pre");pa.className="sub";pa.textContent=JSON.stringify(ev.args||{},null,2);
const pv=document.createElement("div");pv.className="preview-slot";pv.style.display="none";
const lb=document.createElement("p");lb.className="lbl";lb.textContent="响应";lb.style.display="none";
const pb=document.createElement("pre");pb.className="sub tool-res";pb.style.display="none";pb.textContent="";
if(isStreamOutputToolScript(ev.script)){lb.textContent="实时输出";lb.style.display="block";pb.style.display="block";pb.className="sub tool-res chat-run-live-out";pb.textContent="（等待输出…）";}
inner.appendChild(la);inner.appendChild(pa);inner.appendChild(pv);inner.appendChild(lb);inner.appendChild(pb);
if(merged){if(lastLlm&&(lastLlm.reasoningText||"").length>0){var rw=document.createElement("div");rw.className="reasoning-block";var rl=document.createElement("p");rl.className="lbl reasoning-section-lbl";rl.textContent="思考过程";var rp=document.createElement("pre");rp.className="sub reasoning-pre";rp.textContent=lastLlm.reasoningText;rw.appendChild(rl);rw.appendChild(rp);inner.insertBefore(rw,inner.firstChild);}var aw=createToolAnalysisBlock();fillAnalysisBlockFromBuffer(aw);inner.appendChild(aw);removeMergedLlmCard();llmStreamBuffer.consumed=true;}
flushPendingSteps();
body.appendChild(inner);
c.appendChild(h);c.appendChild(body);
h.onclick=function(){c.classList.toggle("open");};
appendStep(c);if(mergeKeepOpen)c.classList.add("open");toolOpen.set(tid,{tag:tag,resLb:lb,resPb:pb,previewSlot:pv,args:ev.args||{},script:ev.script||"",spinWrap:spinWrap,stepTitle:_stt,fileSpan:null,progressBadge:null,liveOutPre:isStreamOutputToolScript(ev.script)?pb:null,cmdInputBar:null,chatRunCard:null,chatRunOutPre:null});
// 流式命令：执行一开始就在左侧对话区建卡，执行结果随后增量刷新
if(isStreamOutputToolScript(ev.script)){ensureChatRunCard(toolOpen.get(tid));}}

function onToolProgress(ev){
var tid=String(ev.tool_call_id!=null?ev.tool_call_id:"").trim();
var o=tid&&toolOpen.get(tid);
if(!o)return;
if(isStreamOutputToolScript(o.script)){
ensureRunCommandLivePanel(o);
if(o.tag&&o.tag.parentNode)o.tag.textContent="进行中 "+(ev.elapsed_sec!=null?ev.elapsed_sec+"s":"");
var tail=String(ev.stdout_tail!=null?ev.stdout_tail:ev.stdoutTail||"");
if(o.liveOutPre&&tail){o.liveOutPre.textContent=tail;scrollMsgsToBottom();}
if(tail)updateChatRunCardOutput(o,tail);
if(isRunCommandToolScript(o.script)&&ev.awaiting_input)renderRunCommandInputBar(o,ev);
return;
}
if(!isHostProgressToolScript(o.script))return;
var sc=ev.scanned;
var te=ev.totalEstimated;
var _rawCf=ev.currentFile;if(_rawCf==null||_rawCf==="")_rawCf=ev.current_file;var _cfOnly=String(_rawCf!=null?_rawCf:"").trim();
if(sc==null&&!_cfOnly)return;
if(o.tag&&o.tag.parentNode)o.tag.textContent="进行中";
var card=findStepCardForToolCall(tid);
if(!card)return;
var titleWrap=card.querySelector(".step-title-wrap");
if(!titleWrap)return;
if(sc!=null){
var progSpan=titleWrap.querySelector(".progress-badge");
if(!progSpan){progSpan=document.createElement("span");progSpan.className="progress-badge";progSpan.style.cssText="margin-left:8px;font-size:12px;color:#888;vertical-align:middle";var _spin=o.spinWrap;if(_spin&&_spin.parentNode===titleWrap)titleWrap.insertBefore(progSpan,_spin);else titleWrap.appendChild(progSpan);}
o.progressBadge=progSpan;
var _badgeTxt="已检查 "+sc+" 个文件";
if(te!=null&&Number(te)>0)_badgeTxt+=" / ~"+te;
progSpan.textContent=_badgeTxt;
}
if(!o.spinWrap){var _sp=document.createElement("span");_sp.className="step-inline-spin";_sp.style.cssText="margin-left:4px;vertical-align:middle";_sp.innerHTML="<span class=\"step-spinner\"></span>";titleWrap.appendChild(_sp);o.spinWrap=_sp;}
if(!o.fileSpan){var _fs=document.createElement("span");_fs.className="step-current-file";_fs.style.cssText="display:inline-block;margin-left:6px;font-size:12px;color:#aaa;max-width:min(360px,55vw);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle";titleWrap.appendChild(_fs);o.fileSpan=_fs;}
if(o.fileSpan){o.fileSpan.textContent=_cfOnly;if(_cfOnly)o.fileSpan.title=_cfOnly;else{try{o.fileSpan.removeAttribute("title");}catch(e){o.fileSpan.title="";}}}
}

function resetTurnState(){closeUserConfirmCardHost();clearLlmAnim();hideChatLoading();lastLlm=null;llmStreamBuffer={round:null,reqHtml:"",resHtml:"",consumed:false};pendingToolTags=[];lastAnalysisTail="";try{toolOpen.forEach(function(o){stopChatRunCountdown(o);});}catch(e){}toolOpen.clear();anyToolThisTurn=false;pendingStepEls=[];streamAssistantEl=null;streamAssistantText="";pendingDeltaSeparator=false;seenDispatchTitle="";endedAwaitingUserConfirm=false;}
function resetSteps(){resetTurnState();stepSeq=0;}

function onToolEnd(ev){const tid=String(ev.tool_call_id!=null?ev.tool_call_id:"").trim();
const o=tid&&toolOpen.get(tid);if(!o)return;
if(o.fileSpan&&o.fileSpan.parentNode){try{o.fileSpan.parentNode.removeChild(o.fileSpan);}catch(_e2){}}o.fileSpan=null;
if(o.spinWrap&&o.spinWrap.parentNode){o.spinWrap.parentNode.removeChild(o.spinWrap);}o.spinWrap=null;
lastAnalysisTail=analysisFollowupPhrase(o.script,o.args||{});
const ok=!!ev.ok;o.tag.innerHTML="";
o.tag.textContent="进行中";
o.tag.className="tag tag-run";
pendingToolTags.push({tag:o.tag,ok:!!ok||!!ev.user_confirm_required});
if(ev.user_confirm_required){o.tag.textContent="待确认";o.tag.className="tag tag-run";}
o.resLb.style.display="block";o.resPb.style.display="block";if(ev.user_confirm_required){try{var _pj=JSON.parse(ev.preview||"{}");var _em=_pj&&_pj.error&&_pj.error.message?String(_pj.error.message):"";var _em2=_em.indexOf("\n\n--help:")>=0?_em.split("\n\n--help:")[0].trim():_em;var slim={ok:_pj.ok,data:_pj.data,error:_pj.error?{code:_pj.error.code,type:_pj.error.type,message:_em2,hint:_pj.error.hint,retryable:_pj.error.retryable}:null};o.resPb.textContent=JSON.stringify(slim,null,2);}catch(e){o.resPb.textContent=ev.preview||"";}}else{try{var _pj2=JSON.parse(ev.preview||"{}");o.resPb.textContent=JSON.stringify(_pj2,null,2);}catch(e){o.resPb.textContent=ev.preview||"";}}
fillToolPreview(o,ev);
try{var _pp=typeof ev.preview==="string"?JSON.parse(ev.preview):ev.preview;var _so=_pp&&_pp.data&&_pp.data.stdout;if(_so&&typeof _so==="string"&&_so.trim()){if(o.chatRunCard&&o.chatRunCard.parentNode){updateChatRunCardOutput(o,_so);}else{ensureChatRunCard(o);updateChatRunCardOutput(o,_so);}}}catch(_e3){}
if(isStreamOutputToolScript(o.script))finalizeChatRunCard(o,ok);
if(ev.user_confirm_required)openUserConfirmModalFromToolEnd(ev);toolOpen.delete(tid);}

async function sendChatMessage(){
if(!ta)return;
var text=String(ta.value||"").trim();
var imgs=pendingChatImages.slice();
if(!text&&!imgs.length)return;
if(isConversationBusy()){alert("当前会话仍在执行中，请等待模型响应、工具执行或确认流程完成后再发送。");return;}
var sendCid=getActiveConversationId();
var display=text||"（截图）";
if(imgs.length)display+=(text?"\n":"")+"[图片 ×"+imgs.length+"]";
var bubble=add("u",display);
var stripEl=null;
if(imgs.length&&window.CWA&&CWA.buildMsgAttachStrip&&bubble){
try{
stripEl=CWA.buildMsgAttachStrip(sendCid,null,imgs);
bubble.appendChild(stripEl);
}catch(e){}
}
ta.value="";
clearPendingChatImages();
autoResizeTextarea();
resetSteps();
showChatLoading();
var tab=findConversationTab(sendCid);
var b=goBtn;if(b)b.disabled=true;
if(tab){tab.abortController={global:true};tab.stopRequested=false;tab.activeRunId="";}
updateTaskControls();
try{
var body={message:text,conversation_id:sendCid,mode:selectedMode,model:selectedModel};
if(imgs.length)body.images=imgs.map(function(it){return{mime:it.mime,data_base64:it.data_base64};});
var r=await fetch("/api/chat/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
if(!r.ok){var _bt="";try{_bt=await r.text();}catch(e){}if(window.DEBUG)console.error("[code-web-agent] /api/chat/send 非 OK",{status:r.status,bodyHead:String(_bt||"").slice(0,500)});withConversationContext(sendCid,function(){hideChatLoading();add("a","HTTP "+r.status);});if(tab){tab.abortController=null;tab.activeRunId="";}return;}
var _sj=await r.json();if(tab&&_sj&&_sj.run_id)tab.activeRunId=String(_sj.run_id||"");
if(stripEl&&_sj&&Array.isArray(_sj.attachments)&&_sj.attachments.length&&window.CWA&&CWA.buildMsgAttachStrip){
try{
var neu=CWA.buildMsgAttachStrip(sendCid,_sj.attachments,null);
if(neu&&stripEl.parentNode)stripEl.parentNode.replaceChild(neu,stripEl);
}catch(e2){}
}
}catch(err){
if(!(err&&err.name==="AbortError")){withConversationContext(sendCid,function(){hideChatLoading();add("a","请求失败: "+(err&&err.message?err.message:String(err)));});}
}finally{
updateTaskControls();
if(typeof renderChatTabs==="function")renderChatTabs();
void persistUsageAccumulator();
}
}

var pendingChatImages=[];
function renderAttachStrip(){
var strip=document.getElementById("attachStrip");
if(!strip)return;
strip.innerHTML="";
if(!pendingChatImages.length){strip.classList.add("hidden");return;}
strip.classList.remove("hidden");
pendingChatImages.forEach(function(it,idx){
var d=document.createElement("div");d.className="attach-thumb";
var img=document.createElement("img");img.src=it.previewUrl;img.alt="attach";
if(window.CWA&&CWA.bindThumbClick)CWA.bindThumbClick(img);
var btn=document.createElement("button");btn.type="button";btn.textContent="×";btn.title="移除";
btn.onclick=function(ev){if(ev){ev.preventDefault();ev.stopPropagation();}pendingChatImages.splice(idx,1);renderAttachStrip();};
d.appendChild(img);d.appendChild(btn);strip.appendChild(d);
});
}
function clearPendingChatImages(){
pendingChatImages.forEach(function(it){try{URL.revokeObjectURL(it.previewUrl);}catch(e){}});
pendingChatImages=[];
renderAttachStrip();
}
function fileToPendingImage(file){
return new Promise(function(resolve,reject){
if(!file||!String(file.type||"").startsWith("image/")){reject(new Error("not image"));return;}
var reader=new FileReader();
reader.onload=function(){
var dataUrl=String(reader.result||"");
var b64=dataUrl.indexOf(",")>=0?dataUrl.split(",")[1]:dataUrl;
resolve({mime:file.type||"image/png",data_base64:b64,previewUrl:URL.createObjectURL(file),name:file.name||"paste.png"});
};
reader.onerror=function(){reject(reader.error||new Error("read fail"));};
reader.readAsDataURL(file);
});
}
async function addPendingImageFiles(fileList){
var files=Array.prototype.slice.call(fileList||[]);
for(var i=0;i<files.length;i++){
if(pendingChatImages.length>=4)break;
try{
var it=await fileToPendingImage(files[i]);
pendingChatImages.push(it);
}catch(e){}
}
renderAttachStrip();
}
(function initChatAttach(){
var attachBtn=document.getElementById("attachBtn");
var attachFile=document.getElementById("attachFile");
if(attachBtn&&attachFile){
attachBtn.addEventListener("click",function(){attachFile.click();});
attachFile.addEventListener("change",function(){void addPendingImageFiles(attachFile.files);attachFile.value="";});
}
if(ta){
ta.addEventListener("paste",function(ev){
var items=ev.clipboardData&&ev.clipboardData.items;
if(!items)return;
var files=[];
for(var i=0;i<items.length;i++){
if(items[i].type&&items[i].type.indexOf("image/")===0){
var f=items[i].getAsFile();
if(f)files.push(f);
}
}
if(!files.length)return;
ev.preventDefault();
void addPendingImageFiles(files);
});
}
})();

(function initClassicMainSplit(){
var STORAGE_KEY="codeWebAgent.classicMainSplit";
var MIN_CHAT_PX=600,MIN_STEPS_PX=300,RESIZER_PX=3;
var mainEl=document.querySelector("main");
var chatEl=mainEl?mainEl.querySelector(".chat"):null;
var sideEl=document.getElementById("classicSidePane")||(mainEl?mainEl.querySelector(".side"):null);
var resizerEl=document.getElementById("classicSplitResizer");
if(!mainEl||!chatEl||!sideEl||!resizerEl)return;
function mainInnerWidth(){var w=mainEl.getBoundingClientRect().width;return w>0?w:window.innerWidth;}
function maxStepsPx(){return Math.max(MIN_STEPS_PX,mainInnerWidth()-RESIZER_PX-MIN_CHAT_PX);}
function defaultStepsPx(){return Math.min(600,Math.round(window.innerWidth*0.46));}
function clampSteps(px){var maxPx=maxStepsPx();if(maxPx<MIN_STEPS_PX)return MIN_STEPS_PX;return Math.max(MIN_STEPS_PX,Math.min(maxPx,Math.round(px)));}
function loadStepsPx(){try{var raw=localStorage.getItem(STORAGE_KEY);if(!raw)return null;var o=JSON.parse(raw);var n=Number(o&&o.stepsPx);return isFinite(n)&&n>0?n:null;}catch(e){return null;}}
function saveStepsPx(px){try{localStorage.setItem(STORAGE_KEY,JSON.stringify({stepsPx:Math.round(px)}));}catch(e){}}
var _savedSteps=loadStepsPx();
var stepsPx=clampSteps(_savedSteps!=null?_savedSteps:defaultStepsPx());
function applyLayout(px){stepsPx=clampSteps(px);sideEl.style.flex="0 0 "+stepsPx+"px";sideEl.style.width=stepsPx+"px";sideEl.style.maxWidth=stepsPx+"px";}
applyLayout(stepsPx);
var drag=null;
resizerEl.addEventListener("mousedown",function(ev){
ev.preventDefault();
drag={startX:ev.clientX,startSteps:stepsPx};
function onMove(e){
if(!drag)return;
applyLayout(drag.startSteps-(e.clientX-drag.startX));
}
function onUp(){
drag=null;
document.removeEventListener("mousemove",onMove);
document.removeEventListener("mouseup",onUp);
saveStepsPx(stepsPx);
}
document.addEventListener("mousemove",onMove);
document.addEventListener("mouseup",onUp);
});
var resizeTimer=null;
window.addEventListener("resize",function(){
if(resizeTimer)clearTimeout(resizeTimer);
resizeTimer=setTimeout(function(){
resizeTimer=null;
var prev=stepsPx;
applyLayout(stepsPx);
if(stepsPx!==prev)saveStepsPx(stepsPx);
},120);
});
})();
initModePicker();
initReasoningPicker();
initKbAndSlashUi();
if(goBtn)goBtn.addEventListener("click",function(){void sendChatMessage();});
if(stopTaskBtn)stopTaskBtn.addEventListener("click",stopCurrentTask);
var nwTopEl=document.getElementById("nwTop");
if(nwTopEl)nwTopEl.addEventListener("click",function(){createConversationTab();});
var hdrIm=document.getElementById("hdrImmersiveBtn");
if(hdrIm){hdrIm.addEventListener("click",function(e){if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button!==0)return;e.preventDefault();if(_layoutPersistTimer){clearTimeout(_layoutPersistTimer);_layoutPersistTimer=null;}try{saveActiveConversationView();}catch(_eH){}storeConversationLayoutLocal();storeConversationId(activeConversationId);void fetch("/api/chat/ui-state",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(conversationLayoutPayload())}).catch(function(){}).finally(function(){window.location.href="/immersive";});});}
if(chatMoreBtn)chatMoreBtn.addEventListener("click",function(){void toggleSessionMenu();});
if(ta){ta.addEventListener("keydown",function(e){if(e.isComposing)return;var sh=slashPop&&!slashPop.classList.contains("hidden");var atp2=getAtPop();var ah=atp2&&atp2.style.visibility!=="hidden";var k=e.key,kl=k.toLowerCase(),ci=(slashPop&&slashPop.children)?slashPop.children:[];if(ah){if(k==="Escape"){e.preventDefault();hideAtPop();return;}if(k==="ArrowDown"||k==="ArrowUp"){e.preventDefault();var ai=getAtList().querySelectorAll(".at-item");var vi=[];for(var i2=0;i2<ai.length;i2++){if(ai[i2].style.display!=="none")vi.push(i2);}if(!vi.length)return;var idx=vi.indexOf(atSelectedIndex);if(idx<0)idx=(k==="ArrowDown"?0:vi.length-1);else if(k==="ArrowDown"){if(idx<vi.length-1)idx++;}else{if(idx>0)idx--;}atSelectedIndex=vi[idx];updateAtSelection();var row=ai[atSelectedIndex];if(row&&row.scrollIntoView)try{row.scrollIntoView({block:"nearest"});}catch(_){}return;}if(k==="ArrowRight"){e.preventDefault();var aiR=getAtList().querySelectorAll(".at-item");var pR=atSelectedIndex;if(pR<0||pR>=aiR.length)pR=0;if(aiR.length&&aiR[pR]&&aiR[pR].dataset.type==="dir")loadAtDir(aiR[pR].dataset.path);return;}if(k==="ArrowLeft"){e.preventDefault();var curL=getAtCurPath().textContent.trim();var parentStored=getAtCurPath().dataset.parent||"";if(!parentStored)return;var pU=parentStored||curL.substring(0,curL.lastIndexOf("/"));if(!pU||pU.length<3){if(pU&&pU.length===2&&pU.charAt(1)===":"){pU=pU+"/";}else{pU=curL;}}if(pU!==curL)atRestoreSelectPath=curL;loadAtDir(pU);return;}if(k==="Enter"||e.code==="Enter"||e.code==="NumpadEnter"){e.preventDefault();var ai2=getAtList().querySelectorAll(".at-item");var pick=atSelectedIndex;if(pick<0||pick>=ai2.length)pick=0;if(ai2.length&&ai2[pick]){var sel=ai2[pick];selectAtFile(sel.dataset.path);}return;}if(atMentionActiveAtCursor()){return;}hideSlashPop();hideAtPop();return;}if(!sh&&!ah){if(!e.shiftKey&&(e.key==="Enter"||e.code==="Enter"||e.code==="NumpadEnter")){e.preventDefault();if(goBtn)goBtn.click();else void sendChatMessage();}return;}if(sh&&k==="Escape"){e.preventDefault();hideSlashPop();return;}if(sh&&(k==="ArrowDown"||k==="ArrowUp")){e.preventDefault();var vi3=[];for(var i3=0;i3<ci.length;i3++){if(ci[i3].style.display!=="none")vi3.push(i3);}if(!vi3.length)return;var idx3=vi3.indexOf(slashSelectedIndex);if(idx3<0)idx3=(k==="ArrowDown"?-1:0);else if(k==="ArrowDown"){if(idx3<vi3.length-1)idx3++;}else{if(idx3>0)idx3--;}var cur3=ci[slashSelectedIndex];if(cur3)cur3.classList.remove("selected");slashSelectedIndex=vi3[idx3];var nxt3=ci[slashSelectedIndex];if(nxt3)nxt3.classList.add("selected");updateSlashPopHints();return;}if(sh&&(k==="Enter"||e.code==="Enter"||e.code==="NumpadEnter")){e.preventDefault();var sel4=ci[slashSelectedIndex];if(sel4&&sel4.dataset&&sel4.dataset.slash){applyMode(sel4.dataset.slash);ta.value="";}else hideSlashPop();return;}if(kl==="a"||kl==="p"||kl==="e"){e.preventDefault();hideSlashPop();ta.value="";if(kl==="a")applyMode("auto");else if(kl==="p")applyMode("plan");else applyMode("execute");return;}hideSlashPop();});ta.addEventListener("blur",function(){if(slashPop&&!slashPop.classList.contains("hidden"))hideSlashPop();});ta.addEventListener("mouseup",function(){if(slashPop&&!slashPop.classList.contains("hidden"))hideSlashPop();});}
document.getElementById("tabSteps")?.addEventListener("click",function(){selectSidePane("steps");});
window.addEventListener("beforeunload",function(){try{saveActiveConversationView();storeConversationLayoutLocal();}catch(e){}});
void loadUsageAccumulator();
initTodoListElements();
startGlobalSse();
void restoreConversationLayoutFromServer();
if(typeof renderChatTabs==="function")renderChatTabs();
_updateUsageBottom();
autoResizeTextarea();

window.testScrollFollow=function(n){
if(!steps){if(window.DEBUG)console.warn("steps 容器未找到");return;}
n=n||25;
if(window.DEBUG)console.log("===== 滚动跟随测试开始: 插入"+n+"个步骤卡片 =====");
var seq=steps.querySelectorAll(".step.card").length+1;
for(var i=0;i<n;i++){
(function(idx){
setTimeout(function(){
var c=document.createElement("div");c.className="step card open";
var h=document.createElement("div");h.className="ch";
var t=document.createElement("span");t.className="tit";
t.textContent="测试步骤 "+(seq+idx)+"：验证滚动跟随 #"+(idx+1);
h.appendChild(t);
var body=document.createElement("div");body.className="card-body";
var inner=document.createElement("div");inner.className="card-body-inner";
var pre=document.createElement("pre");
var _long="";for(var _j=0;_j<40;_j++){_long+="  这是填充行 #"+_j+" 用来撑高卡片，让滚动条出现。步骤 "+(seq+idx)+"\n";}pre.textContent=_long;
inner.appendChild(pre);body.appendChild(inner);c.appendChild(h);c.appendChild(body);
appendStep(c);
if(idx===n-1){if(window.DEBUG)console.log("===== 测试结束 =====");}
},(idx+1)*300);
})(i);
}
};
if(typeof themeUi!=="undefined"){
themeUi.apply();
var _ht=document.getElementById("hdrThemeBtn");
if(_ht)_ht.addEventListener("click",function(){themeUi.toggle();});
}