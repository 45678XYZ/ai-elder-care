import 'dart:async';

import 'package:flutter/material.dart';

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

  /// 這次進入畫面後的完整對話。每一輪問答都往後加，不覆蓋前一輪——
  /// 長輩會想回頭看剛才問過什麼、AI 說過什麼，蓋掉等於對話沒發生過。
  final List<_Message> _messages = [];
  final _scrollCtrl = ScrollController();

  /// 正在辨識中、還沒定案的那一句。定案後移進 [_messages]，這裡清空。
  String _question = '';

  /// 沒聽懂時給長者的提示。內容固定，方便去重（連續失敗不重複洗版）。
  static const _notHeardHint = '我剛剛沒聽清楚，可以再說一次，或用打字。';

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
    _loadElder();
  }

  /// 載入長輩資料後重畫，問候語才叫得出名字而不是「阿公／阿嬤」。
  Future<void> _loadElder() async {
    await AppSession.instance.ensureEldersLoaded();
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _listenTimer?.cancel();
    _scrollCtrl.dispose();
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
        // 辨識失敗對長者只有一種有用的說法：沒聽清楚，再說一次或改打字。
        // 原始錯誤碼幫不上忙，但要留在歷史裡，長輩往回捲才知道哪一句沒進去。
        onError: (_) {
          if (!mounted) return;
          _appendNotHeardHint();
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
    setState(() => _conversationActive = true);
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
    // 只清「正在辨識中」的暫存，不動 _messages——歷史要留著。
    setState(() {
      _setPhase(_Phase.listening);
      _question = '';
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
    // 問題定案，移進歷史；暫存的辨識文字清掉，避免同一句出現兩次。
    setState(() {
      _setPhase(_Phase.thinking);
      _messages.add(_Message(isElder: true, text: question));
      _question = '';
    });
    _scrollToBottom();

    try {
      final result = await _api.ask(question);
      if (!mounted) return;
      setState(() {
        _messages.add(_Message(isElder: false, text: result.answer));
        _setPhase(_Phase.speaking);
      });
      _scrollToBottom();
      await _audio.speak(result.answer);
    } on ApiException catch (_) {
      if (!mounted) return;
      setState(() => _conversationActive = false); // 出錯就停迴圈，避免一直重打
      _appendNotHeardHint();
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
          // 問候語依時段變化；不放語言切換（§5.1）。
          // 不放日期——今日頁的農民曆牌面已經是日期的來源，這裡重複只是雜訊。
          child: Text('${AppSession.instance.displayName}，${_greeting()}！',
              style: text.headlineLarge),
        ),
        // 橫線把問候語與對話區切開，讓下方看得出來是一個「聊天室」而不是同一段內容。
        const Divider(height: 1, thickness: 1.5, color: AppColors.borderDashed),
      ],
    );
  }

  /// 加一則「沒聽清楚」提示。連續失敗時不重複加，否則長輩會被同一句洗版。
  void _appendNotHeardHint() {
    if (_messages.isNotEmpty && _messages.last.text == _notHeardHint) return;
    setState(() =>
        _messages.add(const _Message(isElder: false, text: _notHeardHint)));
    _scrollToBottom();
  }

  /// 新訊息進來後捲到底。等這一幀畫完才捲，否則 maxScrollExtent 還是舊的。
  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollCtrl.hasClients) return;
      _scrollCtrl.animateTo(
        _scrollCtrl.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    });
  }

  Widget _buildConversation(BuildContext context) {
    final text = Theme.of(context).textTheme;

    // 還沒講過話時這塊是空的——一整片留白會讓長輩不確定自己是不是按錯了。
    // 放範例句而不是插圖：它同時回答「這裡能做什麼」和「我該說什麼」。
    if (_messages.isEmpty && _question.isEmpty) {
      return const _ConversationHint();
    }

    // 最後一項是正在辨識中、還沒定案的那句（如果有）。
    final pendingCount = _question.isNotEmpty ? 1 : 0;
    return ListView.builder(
      controller: _scrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      itemCount: _messages.length + pendingCount,
      itemBuilder: (context, i) {
        final isPending = i >= _messages.length;
        final isElder = isPending || _messages[i].isElder;
        return _Bubble(
          isElder: isElder,
          child: Text(
            isPending ? _question : _messages[i].text,
            style: isElder
                ? text.headlineSmall?.copyWith(color: AppColors.onDark)
                : text.headlineSmall,
          ),
        );
      },
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
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
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
}

/// 對話中的一則訊息。
class _Message {
  const _Message({required this.isElder, required this.text});

  final bool isElder;
  final String text;
}

/// 開始對話前的引導。純顯示，不可互動——長者模式的三個互動額度要留給
/// 麥克風、打字、底部分頁。
class _ConversationHint extends StatelessWidget {
  const _ConversationHint();

  /// 挑日常會用到的三句：一句回報、一句身體狀況、一句閒聊，
  /// 讓長輩看得出「什麼都可以說」而不只是查資料。
  static const _examples = [
    '我今天吃過藥了',
    '我有點頭暈',
    '今天天氣怎麼樣',
  ];

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 24, 16, 8),
      children: [
        Text('你可以這樣說',
            style: text.headlineSmall?.copyWith(color: AppColors.inkSecondary)),
        const SizedBox(height: AppSpacing.lg),
        for (final line in _examples) ...[
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.lg, vertical: AppSpacing.lg),
            decoration: const BoxDecoration(
              color: AppColors.card,
              borderRadius: BorderRadius.all(AppRadius.card),
              boxShadow: AppShadows.card,
            ),
            child: Text('「$line」', style: text.headlineSmall),
          ),
          const SizedBox(height: AppSpacing.md),
        ],
      ],
    );
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

  /// 圓球直徑。仍遠大於 60dp 觸控下限，縮小是為了把畫面留給對話內容。
  static const double _size = 84;

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
          // 比圓球大一圈，容納脈動外環的最大半徑。
          width: 124,
          height: 124,
          child: Center(
            child: Stack(
              alignment: Alignment.center,
              children: [
                if (pulsing)
                  AnimatedBuilder(
                    animation: pulse,
                    builder: (_, __) => Container(
                      width: _size + 38 * pulse.value,
                      height: _size + 38 * pulse.value,
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
          width: 40,
          height: 40,
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
      child: Icon(icon, size: 42, color: Colors.white),
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
              hintStyle: TextStyle(color: AppColors.hint),
              filled: true,
              fillColor: AppColors.cardAlt,
              // 不畫框，與其他輸入欄一致；框只在聚焦時出現。
              enabledBorder: InputBorder.none,
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
