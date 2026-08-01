import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../shared/i18n/strings.dart';
import '../../shared/models/elder.dart';
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
    // 順便寫進長者檔案。後端已開放長者本人改這個欄位，寫進去之後換裝置不會退回
    // 舊值。失敗不理會——`/chat` 每次都帶 lang，本機值已經生效了。
    unawaited(AppSession.instance.syncLangFields(langPreference: lang));
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

/// 長者自己切換客語腔調（六腔）。**只在說客語時出現**——華語沒有腔調可言。
///
/// 跟語言鈕的關鍵差別：語言每次 `/chat` 都會帶上去，本機值當下就生效；腔調
/// **後端只讀長者檔案**（api.md：App 不在 `/chat` 傳腔調），所以這顆鈕非得寫進
/// 後端不可，寫失敗就是真的沒生效——因此失敗要講出來，不能默默吞掉。
///
/// 六腔各有獨立的 ASR/TTS 模型端點，選錯不是「口音不太像」而是整句話辨識失敗。
class ElderDialectToggle extends StatefulWidget {
  const ElderDialectToggle({super.key});

  @override
  State<ElderDialectToggle> createState() => _ElderDialectToggleState();
}

class _ElderDialectToggleState extends State<ElderDialectToggle> {
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    // 語言鈕按下去這一區要跟著出現或消失。
    AppSession.langRevision.addListener(_onChanged);
    AppSession.textLangRevision.addListener(_onChanged);
  }

  @override
  void dispose() {
    AppSession.langRevision.removeListener(_onChanged);
    AppSession.textLangRevision.removeListener(_onChanged);
    super.dispose();
  }

  void _onChanged() {
    if (mounted) setState(() {});
  }

  /// 按下去但還沒寫完的那一個。**樂觀更新**：打勾立刻移過去，不等後端。
  ///
  /// 沒有它的話，按下之後要等 `PATCH` 回來（demo 就 400ms 起跳，真後端更久）
  /// 打勾才會動，而這段期間選項是停用的——按下去毫無反應，長輩只會以為壞了、
  /// 接著一直按。今日頁的打勾完成也是同一套做法。
  HakkaDialect? _pending;

  /// 目前的腔調以**長者檔案**為準，不另存本機值——後端讀的就是檔案，本機再存
  /// 一份只會多一個對不上的來源。送出中則先顯示按下去的那一個。
  HakkaDialect get _current =>
      _pending ??
      HakkaDialect.fromValue(AppSession.instance.selectedElder?.hakkaDialect);

  Future<void> _select(HakkaDialect d) async {
    if (_busy) return;
    HapticFeedback.mediumImpact();
    // 先反映再送出：畫面立刻動，失敗才收回。
    setState(() {
      _busy = true;
      _pending = d;
    });

    final ok = await AppSession.instance.syncLangFields(dialect: d.value);
    if (!mounted) return;
    setState(() {
      _busy = false;
      // 成功的話檔案已經是新值，樂觀那份可以退場；失敗則收回，讓打勾跳回
      // 真正生效的那一腔——不能停在他按的那個，否則畫面說改好了其實沒有。
      _pending = null;
    });

    // 成功不打擾：打勾已經移過去了，那就是回饋。失敗才要講——腔調只有檔案
    // 那一份，沒寫進去他下一句話照樣不被辨識，卻以為問題已經解決了。
    if (ok) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.barDark,
        duration: const Duration(seconds: 3),
        content: Text(
          t('沒有改成功，等一下再試一次'),
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
    // 講華語就沒有這一區：腔調是客語才有的概念。
    if (!AppSession.instance.isHakka) return const SizedBox.shrink();

    final text = Theme.of(context).textTheme;
    final current = _current;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(t('我講的腔'),
            style: text.headlineSmall?.copyWith(color: AppColors.inkSecondary)),
        const SizedBox(height: AppSpacing.sm),
        // Wrap 而不是 Row：六個選項一行放不下，而長者字級下換行是必然的。
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            for (final d in HakkaDialect.values)
              _DialectOption(
                label: d.label,
                selected: current == d,
                onTap: _busy ? null : () => _select(d),
              ),
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

/// 腔調選項。跟 [_LangOption] 分開是因為版面約束不同：語言鈕在 `Expanded` 裡
/// 各佔一半，腔調有六個、放在 `Wrap` 裡自己換行，寬度是無界的——`Expanded` 那套
/// 的 `Flexible` 在無界寬度下會直接爆版。
class _DialectOption extends StatelessWidget {
  const _DialectOption({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;

  /// null = 停用（正在送出）。長者連按會送出多個 PATCH，最後生效的是哪一個
  /// 說不準。
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final fg = selected ? Colors.white : AppColors.inkSecondary;

    return Semantics(
      button: true,
      selected: selected,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Container(
          // 長者模式觸控下限 60dp；寬度讓內容決定，Wrap 會排。
          constraints: const BoxConstraints(minHeight: 60, minWidth: 104),
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
            // Wrap 給的是無界寬度，一定要 min，否則 Row 會想佔滿而炸開。
            mainAxisSize: MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (selected) ...[
                Icon(Icons.check, size: 28, color: fg),
                const SizedBox(width: AppSpacing.sm),
              ],
              Text(label, style: text.headlineSmall?.copyWith(color: fg)),
            ],
          ),
        ),
      ),
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
