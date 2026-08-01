import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_service.dart';

/// S2 `/` — 身分宣告的**退路**，不是正常流程的一站。
///
/// 身分現在在註冊頁就問完了（見 [SignUpScreen]），所以照正常路徑走的人不會看到這一頁。
///
/// 為什麼還留著：角色判定看 ID token 的 `elder_id` claim（與後端 auth.py 同一套判準），
/// 但照護者身上本來就沒有這個 claim，所以「沒有 claim」同時代表兩件事——
/// 「我是照護者」與「還沒選過」。而照護者的身分只存在本機（見 AuthService 檔尾
/// TODO(backend)），因此換一台裝置登入、或清掉 App 資料之後，本機就沒有身分記錄了。
/// 這時 `effectiveRole` 是 null，總得有地方能重新問一次，否則使用者會卡在門外。
///
/// 記下之後就不會再出現：之後啟動由 [AuthService.effectiveRole] 直接分流。
class RoleSelectScreen extends StatelessWidget {
  const RoleSelectScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: AppSpacing.pageBody,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Logo pill：accentText 底、白字
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                decoration: const BoxDecoration(
                  color: AppColors.accentText,
                  borderRadius: BorderRadius.all(AppRadius.pill),
                ),
                child: Text('客照e點通',
                    style: text.labelLarge?.copyWith(color: Colors.white)),
              ),
              const SizedBox(height: AppSpacing.xl),
              Text('請問你是？', style: text.headlineLarge),
              const SizedBox(height: AppSpacing.xl),
              // 先寫入身分再導航：redirect 會用 effectiveRole 重新判定落點，
              // 沒寫進去就走，會在下一次判定時被踢回這一頁。
              _RoleCard(
                avatarIcon: Icons.elderly,
                title: '長輩',
                onTap: () async {
                  await AuthService.instance.chooseRole(UserRole.elder);
                  if (context.mounted) context.go('/setup');
                },
              ),
              const SizedBox(height: AppSpacing.lg),
              _RoleCard(
                avatarIcon: Icons.favorite_border,
                title: '家人 / 照護者',
                onTap: () async {
                  await AuthService.instance.chooseRole(UserRole.caregiver);
                  if (context.mounted) context.go('/care/summary');
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RoleCard extends StatelessWidget {
  const _RoleCard({
    required this.avatarIcon,
    required this.title,
    required this.onTap,
  });

  final IconData avatarIcon;
  final String title;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Material(
      color: AppColors.card,
      borderRadius: const BorderRadius.all(AppRadius.card),
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Ink(
          decoration: const BoxDecoration(
            color: AppColors.card,
            borderRadius: BorderRadius.all(AppRadius.card),
            boxShadow: AppShadows.card,
          ),
          child: Container(
            constraints: const BoxConstraints(minHeight: 60),
            padding: const EdgeInsets.all(AppSpacing.xl),
            child: Row(
              children: [
                Container(
                  width: 56,
                  height: 56,
                  decoration: const BoxDecoration(
                    color: AppColors.avatarBg,
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: Icon(avatarIcon, color: AppColors.avatarFg, size: 30),
                ),
                const SizedBox(width: AppSpacing.lg),
                Expanded(child: Text(title, style: text.titleLarge)),
                // chevron：純裝飾，語意由整張卡承載
                const Icon(Icons.chevron_right,
                    color: AppColors.chevron, size: 28),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
