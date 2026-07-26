import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';

/// S2 `/` — 角色選擇（Demo 用；正式由帳號角色決定）。
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
                child: Text('智慧長照陪伴',
                    style: text.labelLarge?.copyWith(color: Colors.white)),
              ),
              const SizedBox(height: AppSpacing.xl),
              Text('請問你是？', style: text.headlineLarge),
              const SizedBox(height: AppSpacing.xl),
              _RoleCard(
                avatarIcon: Icons.elderly,
                title: '長輩',
                onTap: () => context.go('/elder/chat'),
              ),
              const SizedBox(height: AppSpacing.lg),
              _RoleCard(
                avatarIcon: Icons.favorite_border,
                title: '家人 / 照護者',
                onTap: () => context.go('/care/summary'),
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
