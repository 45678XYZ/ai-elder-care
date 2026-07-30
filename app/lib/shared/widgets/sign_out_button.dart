import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../services/auth_service.dart';
import '../../theme/app_theme.dart';

/// 登出。兩種模式共用，只有字級與觸控下限不同。
///
/// 為什麼一定要有：在這之前 [AuthService.signOut] 沒有任何呼叫端，換帳號的唯一
/// 辦法是清掉 App 資料。同一支手機給長輩與家人輪流用（demo、共用平板）就卡住了。
///
/// 一律先問過再登出。長者端尤其不能誤觸——長輩未必記得密碼，登出等於把他鎖在
/// 門外；照護者端也會清掉本機的長輩資料與身分記錄（見 signOut 的說明），
/// 不是隨手可撤的操作。
class SignOutButton extends StatelessWidget {
  const SignOutButton({super.key, this.elderMode = false});

  /// 長者模式：24sp 下限、60dp 觸控（§3）。照護者模式走 Material density。
  final bool elderMode;

  Future<void> _confirmAndSignOut(BuildContext context) async {
    final text = Theme.of(context).textTheme;
    // 長者模式的對話框內文也要守 24sp——它跟畫面上其他字一樣要讀得懂。
    final bodyStyle = elderMode ? text.headlineSmall : text.bodyLarge;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: AppColors.cardAlt,
        title: Text('要登出嗎？',
            style: elderMode ? text.headlineLarge : text.titleLarge),
        content: Text(
          // 照護者這句刻意不提「清掉長輩資料」：長輩名冊、行程與事件都在後端，
          // 登出只丟掉這台裝置上的登入狀態與「上次選了哪位長輩」，重新登入就回來。
          // 講成資料會不見會讓人不敢按，那是不實的恐嚇。
          elderMode ? '登出之後要再輸入一次信箱和密碼才能進來。' : '登出後要重新登入。長輩的資料存在雲端，不會不見。',
          style: bodyStyle,
        ),
        actionsPadding: const EdgeInsets.fromLTRB(
            AppSpacing.md, 0, AppSpacing.md, AppSpacing.md),
        actions: [
          // 取消排在前面、且是低調樣式：誤觸時最容易按到的位置要留給「不做事」。
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            style: TextButton.styleFrom(
              minimumSize: Size(elderMode ? 120 : 88, elderMode ? 60 : 48),
              foregroundColor: AppColors.inkSecondary,
            ),
            child: Text('不要', style: bodyStyle),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: FilledButton.styleFrom(
              minimumSize: Size(elderMode ? 120 : 88, elderMode ? 60 : 48),
              backgroundColor: AppColors.accentText,
              foregroundColor: Colors.white,
            ),
            child: Text('登出', style: bodyStyle?.copyWith(color: Colors.white)),
          ),
        ],
      ),
    );

    if (confirmed != true || !context.mounted) return;

    await AuthService.instance.signOut();
    if (!context.mounted) return;
    // 用 go 不用 push：登出後不該還能用返回鍵回到 App 內頁。
    context.go('/auth/sign-in');
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Center(
      child: TextButton.icon(
        onPressed: () => _confirmAndSignOut(context),
        style: TextButton.styleFrom(
          minimumSize: Size(elderMode ? 200 : 48, elderMode ? 60 : 48),
          foregroundColor: AppColors.inkSecondary,
        ),
        icon: Icon(Icons.logout, size: elderMode ? 24 : 18),
        label: Text(
          '登出',
          style: (elderMode ? text.headlineSmall : text.labelSmall)
              ?.copyWith(color: AppColors.inkSecondary),
        ),
      ),
    );
  }
}
