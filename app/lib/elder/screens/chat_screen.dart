import 'dart:async';

import 'package:flutter/material.dart';

import '../../shared/models/ask_result.dart';
import '../../shared/services/api_client.dart';
import '../../shared/services/api_exception.dart';
import '../../shared/services/audio_service.dart';
import '../../shared/services/session_store.dart';
import '../../shared/services/speech_service.dart';
import '../../theme/app_theme.dart';

/// 對話迴圈階段。
enum _Phase { idle, listening, thinking, speaking }

/// S3 `/elder/chat` — 長者模式語音陪伴主畫面。
///
/// 免手持迴圈：裝置端 ASR 聆聽（zh-TW）→ 送 `ask()`（現在）／`chat()`（之後）→ 裝置端 TTS
/// 唸出回覆 → 唸完自動再聆聽（見 docs/framework.md）。此為第一版華語迴圈，接 RAG PoC 的
/// `/ask`；正式後端上線後把 `ask()` 換成 `chat()`、TTS 換成播 reply_audio_url。
///
/// 長者規格：內文 >=24sp、觸控 >=60dp、可互動元素 <=3、語音有打字備援（§5）。
/// 客語（isHakka）裝置端 ASR 不支援，需改走錄音送後端——目前 TODO，先沿用華語迴圈。
class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen>
    with SingleTickerProviderStateMixin {
  final _api = ApiClient();
  final _speech = SpeechService();
  final _audio = AudioService();

  /// listening／speaking 的脈動外環動畫（§8：600–900ms、可被 disableAnimations 關閉）。
  late final AnimationController _pulse;

  _Phase _phase = _Phase.idle;

  /// 免手持迴圈是否開啟；為 true 時每次唸完回覆會自動再聆聽。
  bool _conversationActive = false;
  bool _micAvailable = false;

  /// 目前這一輪的問題（語音即時辨識或打字），與 AI 回答、錯誤。
  String _question = '';
  AskResult? _result;
  String? _error;

  /// listening 已聆聽秒數。
  Timer? _listenTimer;
  int _listenSeconds = 0;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);
    _initSpeech();
  }

  @override
  void dispose() {
    _listenTimer?.cancel();
    _pulse.dispose();
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

  /// 統一切換階段：管理 listening 秒數計時器。
  /// 狀態變化的螢幕報讀由狀態文字的 liveRegion 承載（§9）。
  void _setPhase(_Phase p) {
    if (p == _Phase.listening) {
      _listenSeconds = 0;
      _listenTimer?.cancel();
      _listenTimer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() => _listenSeconds++);
      });
    } else {
      _listenTimer?.cancel();
      _listenTimer = null;
    }
    _phase = p;
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
    if (mounted) setState(() => _setPhase(_Phase.idle));
  }

  /// 聆聽一句話；靜音斷句後拿到最終文字就送出。
  Future<void> _listenTurn() async {
    if (!_conversationActive || !_micAvailable) return;
    setState(() {
      _setPhase(_Phase.listening);
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
            if (_conversationActive) _listenTurn();
          } else {
            _handleQuestion(q, continueLoop: true);
          }
        }
      },
    );
  }

  /// 送問題到後端，顯示答案並唸出來；[continueLoop] 為 true 且迴圈開啟時唸完自動再聆聽。
  Future<void> _handleQuestion(String question,
      {required bool continueLoop}) async {
    await _speech.stop();
    setState(() {
      _setPhase(_Phase.thinking);
      _question = question;
      _result = null;
      _error = null;
    });

    try {
      final result = await _api.ask(question);
      if (!mounted) return;
      setState(() {
        _result = result;
        _setPhase(_Phase.speaking);
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
    setState(() => _setPhase(_Phase.idle));
    if (continueLoop && _conversationActive) _listenTurn();
  }

  // ---- 打字備援 ----

  Future<void> _submitText(String raw) async {
    final q = raw.trim();
    if (q.isEmpty || _phase != _Phase.idle) return;
    await _handleQuestion(q, continueLoop: false);
  }

  void _openTextInput() {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.cardAlt,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: AppRadius.voicePanel,
      ),
      builder: (_) => _TextInputSheet(onSubmit: _submitText),
    );
  }

  // ---- 狀態文字 ----

  String _statusText(_Phase p) => switch (p) {
        _Phase.listening => '我在聽，說完停一下',
        _Phase.thinking => '聽到了，正在想…',
        _Phase.speaking => '我正在說',
        _Phase.idle => _conversationActive ? '準備中…' : '按一下就可以說話',
      };

  // ---- UI ----

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.of(context).disableAnimations;
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(context),
            Expanded(child: _buildConversation(context)),
            _buildVoicePanel(context, reduceMotion),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 問候語依時段變化；不放語言切換（§5.1）。
          Text('${AppSession.instance.displayName}，${_greeting()}！',
              style: text.headlineLarge),
          const SizedBox(height: 4),
          Text(_todayLabel(),
              style: text.bodyLarge?.copyWith(color: AppColors.inkSecondary)),
        ],
      ),
    );
  }

  Widget _buildConversation(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      children: [
        if (_question.isNotEmpty)
          _Bubble(
            isElder: true,
            child: Text(_question,
                style: text.headlineSmall?.copyWith(color: AppColors.onDark)),
          ),
        if (_error != null)
          _Bubble(
            isElder: false,
            // ASR 錯誤：長者唯一能自行判斷的線索（§5.2）。
            child: Text('我剛剛沒聽清楚，可以再說一次，或用打字。', style: text.headlineSmall),
          )
        else if (_result != null)
          _Bubble(
            isElder: false,
            child: Text(_result!.answer, style: text.headlineSmall),
          ),
      ],
    );
  }

  Widget _buildVoicePanel(BuildContext context, bool reduceMotion) {
    final text = Theme.of(context).textTheme;
    return Container(
      width: double.infinity,
      decoration: const BoxDecoration(
        color: AppColors.cardAlt,
        borderRadius: AppRadius.voicePanel,
        boxShadow: AppShadows.voicePanel,
      ),
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (!_micAvailable)
            Text('這台裝置沒有麥克風，請用下方打字',
                textAlign: TextAlign.center,
                style:
                    text.headlineSmall?.copyWith(color: AppColors.inkSecondary))
          else ...[
            _MicOrb(
              phase: _phase,
              pulse: _pulse,
              reduceMotion: reduceMotion,
              onTap:
                  _conversationActive ? _stopConversation : _startConversation,
            ),
            const SizedBox(height: 12),
            // liveRegion：狀態文字變化時由螢幕報讀器朗讀（§9）。
            Semantics(
              liveRegion: true,
              child: Text(
                _phase == _Phase.listening
                    ? '${_statusText(_phase)}（$_listenSeconds 秒）'
                    : _statusText(_phase),
                textAlign: TextAlign.center,
                style: text.headlineMedium,
              ),
            ),
          ],
          const SizedBox(height: 16),
          // 次要方式：常駐可見的打字按鈕（非隱藏 toggle，§5.1）。
          SizedBox(
            height: 60,
            child: OutlinedButton.icon(
              onPressed: _phase == _Phase.idle ? _openTextInput : null,
              icon: const Icon(Icons.keyboard, size: 28),
              label: Text('改用打字', style: text.headlineSmall),
              style: OutlinedButton.styleFrom(
                foregroundColor: AppColors.ink,
                side: const BorderSide(color: AppColors.border, width: 2),
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(AppRadius.field),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _greeting() {
    final h = DateTime.now().hour;
    if (h < 11) return '早安';
    if (h < 18) return '午安';
    return '晚安';
  }

  String _todayLabel() {
    final now = DateTime.now();
    const week = ['一', '二', '三', '四', '五', '六', '日'];
    return '${now.month}月${now.day}日 星期${week[now.weekday - 1]}';
  }
}

/// 四狀態麥克風。每態外形／內容不同（§5.3），不只靠顏色。
class _MicOrb extends StatelessWidget {
  const _MicOrb({
    required this.phase,
    required this.pulse,
    required this.reduceMotion,
    required this.onTap,
  });

  final _Phase phase;
  final AnimationController pulse;
  final bool reduceMotion;
  final VoidCallback onTap;

  static const double _size = 104;

  @override
  Widget build(BuildContext context) {
    final pulsing = !reduceMotion &&
        (phase == _Phase.listening || phase == _Phase.speaking);
    return Semantics(
      button: true,
      label: switch (phase) {
        _Phase.idle => '開始說話',
        _Phase.listening => '聆聽中，點一下結束',
        _Phase.thinking => '思考中',
        _Phase.speaking => '回覆中，點一下停止',
      },
      child: GestureDetector(
        onTap: onTap,
        child: SizedBox(
          width: 150,
          height: 150,
          child: Center(
            child: Stack(
              alignment: Alignment.center,
              children: [
                if (pulsing)
                  AnimatedBuilder(
                    animation: pulse,
                    builder: (_, __) => Container(
                      width: _size + 46 * pulse.value,
                      height: _size + 46 * pulse.value,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: AppColors.accent
                            .withValues(alpha: 0.35 * (1 - pulse.value)),
                      ),
                    ),
                  ),
                _core(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _core() {
    // thinking：空心紙底 + 5dp 深色外框 + 朱紅小章；其餘：實心 accent。
    if (phase == _Phase.thinking) {
      return Container(
        width: _size,
        height: _size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: AppColors.app,
          border: Border.all(color: AppColors.barDark, width: 5),
        ),
        alignment: Alignment.center,
        child: Container(
          width: 46,
          height: 46,
          decoration: BoxDecoration(
            color: AppColors.accentText,
            borderRadius: BorderRadius.circular(10),
          ),
          alignment: Alignment.center,
          child: const Text('想',
              style: TextStyle(
                  color: Colors.white,
                  fontSize: 24,
                  fontWeight: FontWeight.w900)),
        ),
      );
    }
    final icon = switch (phase) {
      _Phase.speaking => Icons.graphic_eq,
      _Phase.listening => Icons.mic,
      _ => Icons.mic_none,
    };
    return Container(
      width: _size,
      height: _size,
      decoration: const BoxDecoration(
        shape: BoxShape.circle,
        color: AppColors.accent,
        boxShadow: AppShadows.mic,
      ),
      // listening/speaking 加一圈深色外環，靜態也可辨（§5.3 適配）。
      foregroundDecoration: phase == _Phase.idle
          ? null
          : BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: AppColors.accentPressed, width: 5),
            ),
      alignment: Alignment.center,
      child: Icon(icon, size: 52, color: Colors.white),
    );
  }
}

/// 對話泡泡。isElder=true 走深色右對齊、false 走 AI 卡片左對齊。
class _Bubble extends StatelessWidget {
  const _Bubble({required this.isElder, required this.child});

  final bool isElder;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final bubble = Container(
      constraints:
          BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.82),
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      decoration: BoxDecoration(
        color: isElder ? AppColors.barDark : AppColors.card,
        borderRadius: isElder ? AppRadius.bubbleElder : AppRadius.bubbleAi,
        boxShadow: isElder ? null : AppShadows.bubble,
      ),
      child: child,
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment:
            isElder ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isElder) ...[
            Container(
              width: 40,
              height: 40,
              margin: const EdgeInsets.only(top: 2, right: 8),
              decoration: const BoxDecoration(
                color: AppColors.avatarBg,
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: const Icon(Icons.spa, size: 22, color: AppColors.avatarFg),
            ),
          ],
          Flexible(child: bubble),
        ],
      ),
    );
  }
}

/// 打字備援輸入面板（bottom sheet）。長者字級 >=24sp、有明確關閉鈕（§6 modal escape）。
class _TextInputSheet extends StatefulWidget {
  const _TextInputSheet({required this.onSubmit});

  final Future<void> Function(String) onSubmit;

  @override
  State<_TextInputSheet> createState() => _TextInputSheetState();
}

class _TextInputSheetState extends State<_TextInputSheet> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + bottomInset),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('打字給我', style: text.headlineSmall),
              IconButton(
                onPressed: () => Navigator.of(context).pop(),
                iconSize: 32,
                tooltip: '關閉',
                icon: const Icon(Icons.close, color: AppColors.ink),
              ),
            ],
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _controller,
            autofocus: true,
            minLines: 1,
            maxLines: 4,
            style: text.headlineSmall,
            decoration: const InputDecoration(
              hintText: '想問什麼都可以',
              hintStyle: TextStyle(color: AppColors.chevron),
              filled: true,
              fillColor: AppColors.card,
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.all(AppRadius.field),
                borderSide: BorderSide(color: AppColors.border),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.all(AppRadius.field),
                borderSide: BorderSide(color: AppColors.accent, width: 2),
              ),
            ),
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 60,
            child: FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.accentText,
                foregroundColor: Colors.white,
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(AppRadius.field),
                ),
              ),
              onPressed: () {
                final q = _controller.text;
                Navigator.of(context).pop();
                widget.onSubmit(q);
              },
              child: Text('送出', style: text.headlineSmall),
            ),
          ),
        ],
      ),
    );
  }
}
