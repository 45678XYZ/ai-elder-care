import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

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
    final id = _ctrl.text.trim();
    if (id.isEmpty) {
      setState(() => _feedback = const _Feedback.error('請先輸入 ID'));
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
          ? _Feedback.success('已經連結 ${link.caregiver.name}')
          : _Feedback.error('${link.caregiver.name} 已經連結過了');
    } on ApiException catch (e) {
      result = _Feedback.error(
        e.code == ApiErrorCodes.caregiverNotFound
            // 這是最可能發生的錯，而且長輩自己修得好，所以要講得具體。
            ? '找不到這個 ID，請再確認一次'
            : '連結沒有成功，請稍後再試一次',
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

              Text('連結家人', style: text.headlineLarge),
              const SizedBox(height: AppSpacing.md),
              Text(
                '請家人輸入他的 ID，\n就能看到您每天的狀況。',
                style:
                    text.headlineSmall?.copyWith(color: AppColors.inkSecondary),
              ),
              const SizedBox(height: AppSpacing.xl),

              // ID 是英數混合，用 letterSpacing 拉開避免看錯。
              BigTextField(
                controller: _ctrl,
                focusNode: _focus,
                hint: '家人的 ID',
                enabled: !_busy,
                letterSpacing: 2,
                inputFormatters: [
                  // 只留英數與底線，避免長輩誤觸空白或標點。
                  //
                  // 底線非留不可：ID 的格式是 `cg_` 後接 8 個十六進位字元（api.md），
                  // 過濾掉底線的話長輩照著抄也會被吃成 `cg7f3a91c2`，怎麼打都連不上。
                  FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9_]')),
                ],
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: AppSpacing.lg),

              BigButton(label: '加入', busy: _busy, onPressed: _submit),

              if (_feedback != null) ...[
                const SizedBox(height: AppSpacing.lg),
                FeedbackBanner(
                    message: _feedback!.message, isError: _feedback!.isError),
              ],

              if (linked.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xl),
                Text('已經連結的家人', style: text.headlineSmall),
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

/// 送出結果。實際呈現交給共用的 [FeedbackBanner]，這裡只表示「是哪一種結果」。
class _Feedback {
  const _Feedback.success(this.message) : isError = false;
  const _Feedback.error(this.message) : isError = true;

  final String message;
  final bool isError;
}
