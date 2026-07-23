import 'package:flutter/material.dart';

import '../../shared/models/ask_result.dart';
import '../../shared/services/api_client.dart';
import '../../shared/services/api_exception.dart';
import '../../shared/services/audio_service.dart';
import '../../shared/services/speech_service.dart';

/// 對話迴圈階段。
enum _Phase { idle, listening, thinking, speaking }

/// 長者模式——語音對話畫面。
///
/// 免手持迴圈：裝置端 ASR 聆聽（zh-TW）→ 送 `ask()`（現在）／`chat()`（之後）→ 裝置端 TTS
/// 唸出回覆 → 唸完自動再聆聽，全程免觸控（見 docs/framework.md）。此為第一版華語迴圈，
/// 接 RAG PoC 的 `/ask`；正式後端上線後把 `ask()` 換成 `chat()`、TTS 換成播 reply_audio_url。
///
/// 保留打字備援：模擬器無麥克風、或語音辨識不可用時仍可用文字問答。
class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _api = ApiClient();
  final _speech = SpeechService();
  final _audio = AudioService();
  final _controller = TextEditingController();

  _Phase _phase = _Phase.idle;

  /// 免手持迴圈是否開啟；為 true 時每次唸完回覆會自動再聆聽。
  bool _conversationActive = false;
  bool _micAvailable = false;

  /// 目前這一輪的問題（語音即時辨識或打字），與 AI 回答、錯誤。
  String _question = '';
  AskResult? _result;
  String? _error;

  @override
  void initState() {
    super.initState();
    _initSpeech();
  }

  @override
  void dispose() {
    _controller.dispose();
    _speech.cancel();
    _audio.dispose();
    _api.dispose();
    super.dispose();
  }

  Future<void> _initSpeech() async {
    var ok = false;
    try {
      ok = await _speech.init(
        onError: (msg) {
          if (mounted) setState(() => _error = '語音辨識錯誤：$msg');
        },
      );
    } catch (_) {
      ok = false; // 平台不支援或測試環境無外掛，退回打字備援
    }
    if (mounted) setState(() => _micAvailable = ok);
  }

  // ---- 免手持語音迴圈 ----

  void _startConversation() {
    setState(() {
      _conversationActive = true;
      _error = null;
    });
    _listenTurn();
  }

  Future<void> _stopConversation() async {
    setState(() => _conversationActive = false);
    await _speech.stop();
    await _audio.stop();
    if (mounted) setState(() => _phase = _Phase.idle);
  }

  /// 聆聽一句話；靜音斷句後拿到最終文字就送出。
  Future<void> _listenTurn() async {
    if (!_conversationActive || !_micAvailable) return;
    setState(() {
      _phase = _Phase.listening;
      _question = '';
      _result = null;
      _error = null;
    });

    var handled = false; // 每輪只處理一次最終結果
    await _speech.listen(
      onResult: (text, isFinal) {
        if (!mounted) return;
        setState(() => _question = text);
        if (isFinal && !handled) {
          handled = true;
          final q = text.trim();
          if (q.isEmpty) {
            // 沒聽到內容，若迴圈還開著就再聽一次
            if (_conversationActive) _listenTurn();
          } else {
            _handleQuestion(q, continueLoop: true);
          }
        }
      },
    );
  }

  /// 送問題到後端，顯示答案並唸出來；[continueLoop] 為 true 且迴圈開啟時，唸完自動再聆聽。
  Future<void> _handleQuestion(String question,
      {required bool continueLoop}) async {
    await _speech.stop();
    setState(() {
      _phase = _Phase.thinking;
      _question = question;
      _result = null;
      _error = null;
    });

    try {
      final result = await _api.ask(question);
      if (!mounted) return;
      setState(() {
        _result = result;
        _phase = _Phase.speaking;
      });
      await _audio.speak(result.answer);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _conversationActive = false; // 出錯就停迴圈，避免一直重打
      });
    }

    if (!mounted) return;
    setState(() => _phase = _Phase.idle);
    if (continueLoop && _conversationActive) _listenTurn();
  }

  // ---- 打字備援 ----

  Future<void> _submitText() async {
    final q = _controller.text.trim();
    if (q.isEmpty || _phase != _Phase.idle) return;
    _controller.clear();
    await _handleQuestion(q, continueLoop: false);
  }

  // ---- UI ----

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('衛教問答（PoC）')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _buildMicButton(),
            const SizedBox(height: 8),
            _buildStatus(context),
            const SizedBox(height: 16),
            Expanded(child: _buildResult(context)),
            const SizedBox(height: 8),
            _buildTextFallback(),
          ],
        ),
      ),
    );
  }

  Widget _buildMicButton() {
    if (!_micAvailable) {
      return const Text(
        '此裝置無法使用語音，請用下方打字',
        textAlign: TextAlign.center,
        style: TextStyle(color: Colors.grey),
      );
    }
    final active = _conversationActive;
    return FilledButton.icon(
      onPressed: active ? _stopConversation : _startConversation,
      icon: Icon(active ? Icons.stop : Icons.mic),
      label: Text(active ? '停止對話' : '開始語音對話'),
      style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(56),
        backgroundColor: active ? Theme.of(context).colorScheme.error : null,
      ),
    );
  }

  Widget _buildStatus(BuildContext context) {
    final label = switch (_phase) {
      _Phase.listening => '聆聽中…請說話',
      _Phase.thinking => '思考中…',
      _Phase.speaking => '回答中…',
      _Phase.idle => _conversationActive ? '準備中…' : '按上方按鈕開始，或用下方打字',
    };
    return Text(
      label,
      textAlign: TextAlign.center,
      style: Theme.of(context).textTheme.bodySmall,
    );
  }

  Widget _buildResult(BuildContext context) {
    if (_error != null) {
      return Center(
        child: Text(
          '發生錯誤：$_error',
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      );
    }
    final result = _result;
    return ListView(
      children: [
        if (_question.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text('你問：$_question',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ),
        if (_phase == _Phase.thinking) const LinearProgressIndicator(),
        if (result != null) ...[
          SelectableText(result.answer,
              style: const TextStyle(fontSize: 18, height: 1.5)),
          if (result.sources.isNotEmpty) ...[
            const SizedBox(height: 24),
            Text('引用來源', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            for (final source in result.sources)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text('• ${source.title}'),
              ),
          ],
        ],
      ],
    );
  }

  Widget _buildTextFallback() {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: _controller,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => _submitText(),
            decoration: const InputDecoration(
              hintText: '也可以打字問，例如：高血壓要注意什麼？',
              border: OutlineInputBorder(),
              isDense: true,
            ),
          ),
        ),
        const SizedBox(width: 8),
        IconButton.filled(
          onPressed: _phase == _Phase.idle ? _submitText : null,
          icon: const Icon(Icons.send),
        ),
      ],
    );
  }
}
