import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../shared/i18n/strings.dart';
import '../../shared/services/session_store.dart';
import '../../theme/app_theme.dart';

/// 長者自己切換**說話**的語言：華語 ↔ 客語。
///
/// 按下去改的是 `AppSession.lang`，也就是長輩開口之後走哪一條路——華語用裝置端
/// 辨識、客語錄音送後端（見 `chat_screen`）。
///
/// **畫面文字是另一顆鈕，不共用這一顆。** 講客語的長輩不一定讀得懂客語漢字——
/// 有人講客語但只認得一般漢字，把兩件事綁在同一個開關，等於逼這種人在「聽不懂
/// 語音」和「看不懂畫面」之間二選一。所以標籤這裡寫「客語」（怎麼說），
/// 文字那顆才寫「客語漢字」（怎麼寫），見 [ElderTextLangToggle]。
///
/// 為什麼放在長者端而不是只留在照護者的管理頁（原本 CLAUDE.md 的約束）：真正
/// 知道自己講哪一種話的是長輩本人，而照護者設錯時長輩沒有任何自救的辦法。代價是
/// 本機值會蓋過照護者設的 `lang_preference`，見 [AppSession.setLang]。
///
/// 長者規格：兩個選項各自 >=60dp 觸控、>=24sp 字；選中狀態同時靠底色**和**打勾
/// icon 承載，不只靠顏色（§9）。
class ElderLangToggle extends StatefulWidget {
  const ElderLangToggle({super.key});

  @override
  State<ElderLangToggle> createState() => _ElderLangToggleState();
}

class _ElderLangToggleState extends State<ElderLangToggle> {
  static const _options = [('zh-TW', '中文'), ('hak', '客語')];

  // 自己訂閱書寫語言：這顆鈕在今日頁是 `const ElderLangToggle()`，而 const widget
  // 會被正規化成同一個實例——父層 setState 重建時 Flutter 比到 identical(new, old)
  // 就整棵子樹跳過，於是「我說的話」會停在建立時那一版，按了旁邊那顆文字鈕也不動。
  // 拿掉 const 也能修，但下一個人很容易再加回去，訂閱才擋得住。
  @override
  void initState() {
    super.initState();
    AppSession.textLangRevision.addListener(_onTextLangChanged);
  }

  @override
  void dispose() {
    AppSession.textLangRevision.removeListener(_onTextLangChanged);
    super.dispose();
  }

  void _onTextLangChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _select(String lang) async {
    final changed = _current != lang;
    // 語言是「按了之後畫面幾乎沒變化」的那種設定，觸覺回饋在這裡不是裝飾——
    // 它是長輩唯一能立刻確認「我按到了」的訊號（§13）。按的是目前這個語言也要給，
    // 沒有任何反應會讓長輩以為機器壞了、接著一直按。
    HapticFeedback.mediumImpact();
    // **就算按的是目前這個語言也要送進 [AppSession.setLang]**：它同時記下「長者
    // 自己選過了」，而那個旗標決定照護者之後改 `lang_preference` 時要不要蓋過
    // 長輩的選擇。早退在這裡等於把長輩明確按下的那一次當作沒發生。
    await AppSession.instance.setLang(lang);
    if (!mounted) return;
    setState(() {});
    // 只有真的換了才說話：沒換卻跳「接下來用中文跟我說話」是在報告一件沒發生的事。
    if (!changed) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        duration: const Duration(seconds: 3),
        content: Text(
          t(lang == 'hak' ? '好，接下來用客語跟我說話' : '好，接下來用中文跟我說話'),
          style: Theme.of(context)
              .textTheme
              .headlineSmall
              ?.copyWith(color: AppColors.onDark),
        ),
      ),
    );
  }

  /// 目前實際生效的語言。看 [AppSession.isHakka] 而不是 `lang`——長者還沒選過時
  /// 生效的是照護者設的 `lang_preference`，`lang` 這時只是預設值，拿它顯示會騙人。
  String get _current => AppSession.instance.isHakka ? 'hak' : 'zh-TW';

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final current = _current;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(t('我說的話'),
            style: text.headlineSmall?.copyWith(color: AppColors.inkSecondary)),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            for (final (value, label) in _options) ...[
              Expanded(
                child: _LangOption(
                  label: t(label),
                  selected: current == value,
                  onTap: () => _select(value),
                ),
              ),
              if (value != _options.last.$1)
                const SizedBox(width: AppSpacing.sm),
            ],
          ],
        ),
      ],
    );
  }
}

/// 長者自己切換**畫面文字**的書寫語言：一般漢字 ↔ 客語漢字。
///
/// 跟 [ElderLangToggle] 刻意分開，理由見那邊的說明：講客語不等於讀得懂客語漢字。
///
/// 缺譯的句子會原樣留在華語（見 `shared/i18n/strings.dart` 的 [missingFromHakka]），
/// 所以選了客語漢字之後畫面可能中客夾雜。這比整句變空白或亂碼好——長輩至少
/// 還讀得到內容，而不是看到一片空的。
class ElderTextLangToggle extends StatefulWidget {
  const ElderTextLangToggle({super.key});

  @override
  State<ElderTextLangToggle> createState() => _ElderTextLangToggleState();
}

class _ElderTextLangToggleState extends State<ElderTextLangToggle> {
  static const _options = [('zh-TW', '中文'), ('hak', '客語漢字')];

  // 跟 [ElderLangToggle] 同一個理由（見那邊的說明）：這顆也是 const，不能只靠
  // 自己按下去的那次 setState——別的地方改了書寫語言時它一樣要跟著換。
  @override
  void initState() {
    super.initState();
    AppSession.textLangRevision.addListener(_onTextLangChanged);
  }

  @override
  void dispose() {
    AppSession.textLangRevision.removeListener(_onTextLangChanged);
    super.dispose();
  }

  void _onTextLangChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _select(String lang) async {
    final changed = AppSession.instance.textLang != lang;
    HapticFeedback.mediumImpact();
    await AppSession.instance.setTextLang(lang);
    if (!mounted) return;
    setState(() {});
    if (!changed) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        duration: const Duration(seconds: 3),
        content: Text(
          // 已經切過去了，所以這句提示本身就是新語言——長輩讀得懂它，
          // 等於順便確認了「我看得懂這種字」。
          t(lang == 'hak' ? '畫面的字改成客語漢字了' : '畫面的字改成中文了'),
          style: Theme.of(context)
              .textTheme
              .headlineSmall
              ?.copyWith(color: AppColors.onDark),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final current = AppSession.instance.textLang;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(t('畫面的字'),
            style: text.headlineSmall?.copyWith(color: AppColors.inkSecondary)),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            for (final (value, label) in _options) ...[
              Expanded(
                child: _LangOption(
                  label: t(label),
                  selected: current == value,
                  onTap: () => _select(value),
                ),
              ),
              if (value != _options.last.$1)
                const SizedBox(width: AppSpacing.sm),
            ],
          ],
        ),
      ],
    );
  }
}

class _LangOption extends StatelessWidget {
  const _LangOption({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final fg = selected ? Colors.white : AppColors.inkSecondary;

    return Semantics(
      button: true,
      selected: selected,
      label: label,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Container(
          constraints: const BoxConstraints(minHeight: 60),
          padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md, vertical: AppSpacing.md),
          decoration: BoxDecoration(
            color: selected ? AppColors.accentText : AppColors.card,
            borderRadius: const BorderRadius.all(AppRadius.card),
            border: Border.all(
              color:
                  selected ? AppColors.accentText : AppColors.borderInteractive,
              width: 1,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              // 選中才給勾；未選取留空白而不是換一個灰 icon——兩個 icon 並列
              // 反而要比對才知道哪個是選的，空白對比更直接。
              if (selected) ...[
                Icon(Icons.check, size: 28, color: fg),
                const SizedBox(width: AppSpacing.sm),
              ],
              // Flexible：兩倍字級時長標籤要換行而不是撐破格子。
              Flexible(
                child: Text(
                  label,
                  textAlign: TextAlign.center,
                  style: text.headlineSmall?.copyWith(color: fg),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
