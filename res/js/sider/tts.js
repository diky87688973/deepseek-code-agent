/* 语音播放：接收 SSE audio 事件，顺序排队播放，支持每会话独立开关 */
(function (w) {
  "use strict";
  var IMM = (w.IMM = w.IMM || {});
  var _audioQueue = [];
  var _playing = false;
  var _currentAudio = null;

  // 每会话语音开关（默认关闭）
  var _audioEnabled = {};
  _audioEnabled._default = false;

  IMM.setAudioEnabled = function (cid, on) {
    if (cid) _audioEnabled[cid] = !!on;
  };

  IMM.isAudioEnabled = function (cid) {
    return _audioEnabled[cid] !== false;
  };

  function _playNext() {
    if (_playing || _audioQueue.length === 0) return;
    var item = _audioQueue.shift();
    var cid = item.cid;
    if (IMM.isAudioEnabled(cid)) {
      _playing = true;
      var audio = new Audio(item.url);
      _currentAudio = audio;
      audio.onended = function () {
        if (_currentAudio === audio) _currentAudio = null;
        _playing = false;
        _playNext();
      };
      audio.onerror = function () {
        if (_currentAudio === audio) _currentAudio = null;
        _playing = false;
        _playNext();
      };
      audio.play().catch(function (err) {
        console.warn("[TTS]", err);
        if (_currentAudio === audio) _currentAudio = null;
        _playing = false;
        _playNext();
      });
    } else {
      _playNext();
    }
  }

  function playAudio(cid, audioBase64, mimeType) {
    if (!audioBase64 || typeof audioBase64 !== "string") return;
    // 首次遇到 cid 时注册，继承全局默认状态
    if (cid && _audioEnabled[cid] === undefined) {
      _audioEnabled[cid] = _audioEnabled._default !== false;
    }
    try {
      var mime = mimeType || "audio/mp3";
      var dataUrl = "data:" + mime + ";base64," + audioBase64;
      _audioQueue.push({ cid: cid, url: dataUrl });
      if (!_playing) _playNext();
    } catch (err) {
      console.warn("[TTS]", err);
    }
  }
  IMM.playAudio = playAudio;

  function toggleImmersiveTts() {
    var allCids = Object.keys(_audioEnabled);
    // 过滤掉 _default 内部 key，只处理真实会话
    var realCids = [];
    for (var ri = 0; ri < allCids.length; ri++) {
      if (allCids[ri] !== "_default") realCids.push(allCids[ri]);
    }
    var on;
    if (realCids.length === 0) {
      // 无已知会话时，切换全局默认状态
      _audioEnabled._default = _audioEnabled._default === undefined ? false : !_audioEnabled._default;
      on = _audioEnabled._default !== false;
    } else {
      on = IMM.isAudioEnabled(realCids[0]) ? false : true;
      // 同步全局默认状态，使新会话继承
      _audioEnabled._default = on;
      for (var i = 0; i < realCids.length; i++) {
        IMM.setAudioEnabled(realCids[i], on);
      }
    }
    // 关声音时：停止当前播放 + 清空队列
    if (!on) {
      if (_currentAudio) {
        try { _currentAudio.pause(); } catch (e) {}
        _currentAudio = null;
      }
      _audioQueue.length = 0;
      _playing = false;
    }
    var btn = document.getElementById("immTtsBtn");
    if (btn) {
      btn.textContent = on ? "🔊" : "🔇";
      btn.className = "tts-toggle-btn " + (on ? "on" : "off");
    }
    // 通知后端（只传真实会话）
    for (var j = 0; j < realCids.length; j++) {
      fetch("/api/tts/state", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: realCids[j], enabled: on })
      }).catch(function(ex){ console.warn("[TTS]", ex); });
    }
  }
  IMM.toggleImmersiveTts = toggleImmersiveTts;
})(window);
