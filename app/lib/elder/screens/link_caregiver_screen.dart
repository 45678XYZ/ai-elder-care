import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../shared/i18n/strings.dart';
import '../../shared/models/caregiver.dart';
import '../../shared/services/api_error_codes.dart';
import '../../shared/services/api_exception.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/form_widgets.dart';
import '../../theme/app_theme.dart';

/// `/elder/link` — 連結家人。
///
/// 實際操作者是照護者（拿長輩的手機輸入自己的 ID），但這一頁長在長者 App 裡，
/// 長輩隨時可能自己點進來，所以整頁照長者規格做：字級 >=24sp、觸控 >=60dp。
///
/// 可互動元素恰好三個——返回、輸入框、加入——已連結清單是唯讀的。
/// 不提供「移除」：解除家人連結的後果嚴重，不該由長輩在自己手機上單獨完成。
class LinkCaregiverScreen extends StatefulWidget {
  const LinkCaregiverScreen({super.key});

  @override
  State<LinkCaregiverScreen> createState() => _LinkCaregiverScreenState();
}

class _LinkCaregiverScreenState extends State<LinkCaregiverScreen> {
  final _ctrl = TextEditingController();
  final _focus = FocusNode();

  /// 送出後的結果訊息。長者模式不用 SnackBar——它字小、會自己消失，
  /// 長輩來不及讀。改用留在畫面上的大字區塊。
  _Feedback? _feedback;
  bool _busy = false;

  @override
  void dispose() {
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    _loadLinked();
  }

  /// 已連結的家人來自後端（`GET /elders/{id}/caregivers`），進頁面時載一次。
  ///
  /// 失敗不擋畫面：連結的入口本身還是要能用，清單載不出來頂多是少看到已連結的那幾位。
  Future<void> _loadLinked() async {
    try {
      await AppSession.instance.ensureCaregiversLoaded();
    } catch (_) {
      // 清單載不出來就先空著
    }
    if (mounted) setState(() {});
  }

  Future<void> _submit() async {
    final id = _normalizeCaregiverId(_ctrl.text);
    if (id.isEmpty) {
      setState(() => _feedback = _Feedback.error(t('請先輸入 ID')));
      return;
    }

    setState(() {
      _busy = true;
      _feedback = null;
    });

    // 三種結果，三句不同的話。原本只有兩種（加進去了／已經有了），任何字串都會被
    // 當成有效 ID——長輩少抄一碼，畫面照樣說「連結成功」，而那位家人永遠收不到資料。
    _Feedback result;
    try {
      final link = await AppSession.instance.linkCaregiver(id);
      result = link.isNew
          ? _Feedback.success(t1('已經連結 {}', link.caregiver.name))
          : _Feedback.error(t1('{} 已經連結過了', link.caregiver.name));
    } on ApiException catch (e) {
      // 兩種錯都是「這組 ID 有問題」，長輩自己修得好，所以要講得具體。
      //
      // 400 INVALID_PARAMETER 也算進來：後端要求 `cg_` 前綴，長輩若只抄了後半段
      // 而 [_normalizeCaregiverId] 又補不出來（例如抄成 7 碼），拿到的就是 400。
      // 那時說「請稍後再試」等於叫他重試一個永遠不會成功的動作。
      final wrongId = e.code == ApiErrorCodes.caregiverNotFound ||
          e.code == ApiErrorCodes.invalidParameter;
      result = _Feedback.error(
        wrongId ? t('找不到這個 ID，請再確認一次') : t('連結沒有成功，請稍後再試一次'),
      );
    }

    if (!mounted) return;
    final ok = !result.isError;
    setState(() {
      _busy = false;
      if (ok) _ctrl.clear();
      _feedback = result;
    });
    // 成功後收鍵盤，讓長輩看得到剛加進清單的那一筆。
    if (ok) _focus.unfocus();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final linked = AppSession.instance.linkedCaregivers;

    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: AppSpacing.pageBody,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              BigBackButton(onTap: () => context.pop()),
              const SizedBox(height: AppSpacing.xl),

              Text(t('連結家人'), style: text.headlineLarge),
              const SizedBox(height: AppSpacing.md),
              Text(
                t('請家人輸入他的 ID，\n就能看到您每天的狀況。'),
                style:
                    text.headlineSmall?.copyWith(color: AppColors.inkSecondary),
              ),
              const SizedBox(height: AppSpacing.xl),

              // ID 是英數混合，用 letterSpacing 拉開避免看錯。
              BigTextField(
                controller: _ctrl,
                focusNode: _focus,
                hint: t('家人的 ID'),
                enabled: !_busy,
                letterSpacing: 2,
                inputFormatters: [
                  // 只留英數與連接符號，避免長輩誤觸空白或標點。
                  //
                  // 底線非留不可：ID 的格式是 `cg_` 後接 8 個十六進位字元（api.md），
                  // 過濾掉底線的話長輩照著抄也會被吃成 `cg7f3a91c2`，怎麼打都連不上。
                  //
                  // 破折號一起收，再於下一個 formatter 轉成底線：手機中文鍵盤上
                  // 「－」按得到、「＿」往往要切到符號頁再翻一層，長輩打不出來就卡死在
                  // 這一頁——而這一頁是他連上家人的唯一入口。ID 本身不含破折號，
                  // 收下來只可能是想打底線，直接視為同一個字比讓他打不出來好。
                  // 這裡放行的橫線集合必須與 [_DashToUnderscoreFormatter] 完全一致
                  // ——formatter 是**依序**執行的，這一關先擋掉的字元，下一關就沒有
                  // 機會轉成底線了。
                  FilteringTextInputFormatter.allow(
                      RegExp('[A-Za-z0-9_$_dashChars]')),
                  _DashToUnderscoreFormatter(),
                ],
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: AppSpacing.lg),

              BigButton(label: t('加入'), busy: _busy, onPressed: _submit),

              if (_feedback != null) ...[
                const SizedBox(height: AppSpacing.lg),
                FeedbackBanner(
                    message: _feedback!.message, isError: _feedback!.isError),
              ],

              if (linked.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xl),
                Text(t('已經連結的家人'), style: text.headlineSmall),
                const SizedBox(height: AppSpacing.md),
                for (final c in linked) _LinkedRow(caregiver: c),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// 已連結的一筆。唯讀，不可點。
///
/// 顯示名字而不是 ID：`cg_7f3a91c2` 對長輩沒有任何意義，看到「陳志明」才知道
/// 連上的是誰、有沒有連錯人。ID 用小字附在下面，家人要對照時看得到。
class _LinkedRow extends StatelessWidget {
  const _LinkedRow({required this.caregiver});

  final Caregiver caregiver;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: const BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.all(AppRadius.card),
        boxShadow: AppShadows.card,
      ),
      child: Row(
        children: [
          const Icon(Icons.check_circle, size: 32, color: AppColors.successFg),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(caregiver.name, style: text.headlineSmall),
                const SizedBox(height: 2),
                Text(
                  caregiver.caregiverId,
                  style: text.bodyMedium?.copyWith(
                    color: AppColors.inkSecondary,
                    letterSpacing: 1,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 把長輩打進去的內容整理成後端認得的 `caregiver_id`。
///
/// 兩件事：
///
/// 1. **統一小寫、去頭尾空白**。後端自己也會做（`.strip().lower()`），這裡先做是為了
///    讓補前綴的判斷拿到乾淨的輸入。
/// 2. **缺 `cg_` 前綴時補上**。後端要求前綴，沒有就回 400 `INVALID_PARAMETER`
///    ——而長輩看著照護者手機上的 `cg_7f3a91c2` 抄，很容易只抄後半段。
///
/// 補前綴的條件很嚴：剩下的部分必須**正好是 8 個十六進位字元**，也就是 ID 的實際
/// 格式（api.md）。條件成立才代表他抄的是後半段；否則一律原樣送出，讓後端照常回
/// 404 `CAREGIVER_NOT_FOUND`。**猜錯而自動補出一個看似合法的 ID，會把「打錯字」
/// 變成「查無此人」，反而更難查。**
String _normalizeCaregiverId(String raw) {
  final id = raw.trim().toLowerCase();
  if (id.isEmpty || id.startsWith('cg_')) return id;
  return RegExp(r'^[0-9a-f]{8}$').hasMatch(id) ? 'cg_$id' : id;
}

/// 會被當成底線處理的字元：半形連字號、Unicode 的各種破折號、全形連字號、全形底線。
///
/// 一個個列出來而不是用範圍：範圍寫在字元類別裡容易連帶收進不相干的符號，
/// 而這個欄位的內容會直接拿去跟後端的 ID 比對，多收一個字元就是一次連不上。
const _dashChars = r'\-‐‑‒–—―－＿';

/// 把使用者打出來的各種「橫線」一律當成底線。
///
/// ID 的格式是 `cg_` 後接 8 個十六進位字元，本身不含破折號——所以在這個欄位裡
/// 打出橫線只可能是想打底線卻找不到。手機中文鍵盤上半形底線常要切到符號頁再翻一層，
/// 長輩打不出來就會卡死在這一頁，而這是他連上家人的唯一入口。
///
/// 全形底線（＿）一併正規化：注音鍵盤的符號頁給的是全形，直接送出去後端比對不到。
class _DashToUnderscoreFormatter extends TextInputFormatter {
  static final _dashes = RegExp('[$_dashChars]');

  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
    final text = newValue.text.replaceAll(_dashes, '_');
    if (text == newValue.text) return newValue;
    // 長度不變（一律一對一替換），游標位置照原樣帶著走。
    return TextEditingValue(text: text, selection: newValue.selection);
  }
}

/// 送出結果。實際呈現交給共用的 [FeedbackBanner]，這裡只表示「是哪一種結果」。
class _Feedback {
  const _Feedback.success(this.message) : isError = false;
  const _Feedback.error(this.message) : isError = true;

  final String message;
  final bool isError;
}
