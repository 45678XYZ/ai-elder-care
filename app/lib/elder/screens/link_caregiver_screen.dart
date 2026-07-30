import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

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

    final added = await AppSession.instance.linkCaregiver(id);

    if (!mounted) return;
    setState(() {
      _busy = false;
      if (added) {
        _ctrl.clear();
        _feedback = const _Feedback.success('連結成功');
      } else {
        _feedback = const _Feedback.error('這個 ID 已經連結過了');
      }
    });
    // 成功後收鍵盤，讓長輩看得到剛加進清單的那一筆。
    if (added) _focus.unfocus();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final linked = AppSession.instance.linkedCaregiverIds;

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
                  // 只留英數與連字號，避免長輩誤觸空白或標點。
                  FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9\-]')),
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
                for (final id in linked) _LinkedRow(caregiverId: id),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// 已連結的一筆。唯讀，不可點。
class _LinkedRow extends StatelessWidget {
  const _LinkedRow({required this.caregiverId});

  final String caregiverId;

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
            child: Text(caregiverId,
                style: text.headlineSmall?.copyWith(letterSpacing: 1)),
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
