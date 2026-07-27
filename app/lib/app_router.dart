import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'caregiver/caregiver_shell.dart';
import 'caregiver/screens/elders_screen.dart';
import 'caregiver/screens/stats_screen.dart';
import 'caregiver/screens/summaries_screen.dart';
import 'caregiver/screens/timeline_screen.dart';
import 'caregiver/screens/setup_screen.dart';
import 'elder/elder_shell.dart';
import 'elder/screens/chat_screen.dart';
import 'elder/screens/link_caregiver_screen.dart';
import 'elder/screens/today_screen.dart';
import 'shared/screens/role_select_screen.dart';
import 'shared/screens/sign_in_screen.dart';
import 'shared/screens/sign_up_screen.dart';
import 'shared/screens/verify_email_screen.dart';

/// App 導覽。畫面編號與路徑對照見 HANDOFF.md 畫面清單。
/// 兩模式各自一個 StatefulShellRoute，切 tab 保留畫面狀態與捲動位置（§6）。
final _elderNavKey = GlobalKey<NavigatorState>();
final _careNavKey = GlobalKey<NavigatorState>();

GoRouter buildRouter({String initialLocation = '/setup'}) => GoRouter(
      initialLocation: initialLocation,
      routes: [
        // 認證。長者與照護者共用同一組畫面，登入後才由 token 的 elder_id claim 分流。
        GoRoute(path: '/auth/sign-in', builder: (_, __) => const SignInScreen()),
        GoRoute(path: '/auth/sign-up', builder: (_, __) => const SignUpScreen()),
        GoRoute(
          path: '/auth/verify',
          // 信箱由上一頁用 extra 帶進來。直接開網址（例如重整）時沒有 extra，
          // 這時退回註冊頁重走一次，比帶著空信箱送出驗證碼要好。
          builder: (context, state) {
            final email = state.extra;
            if (email is! String || email.isEmpty) return const SignUpScreen();
            return VerifyEmailScreen(email: email);
          },
        ),

        // S1 首次設定（照護者填寫，只在首次安裝出現）
        GoRoute(path: '/setup', builder: (_, __) => const SetupScreen()),

        // S2 角色選擇（Demo 用；正式由帳號角色決定）
        GoRoute(path: '/', builder: (_, __) => const RoleSelectScreen()),

        // 連結家人。刻意放在 shell 之外——它是蓋在長者 tab 上的一次性流程，
        // 不該變成第三個常駐 tab（長者模式一頁最多 3 個可互動元素）。
        // 從今天頁 push 進來，返回時回到原本的 tab 與捲動位置。
        GoRoute(
            path: '/elder/link', builder: (_, __) => const LinkCaregiverScreen()),

        // S3+S4 長者模式：底部 2 tab
        StatefulShellRoute.indexedStack(
          builder: (_, __, shell) => ElderShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              navigatorKey: _elderNavKey,
              routes: [
                GoRoute(
                    path: '/elder/chat',
                    builder: (_, __) => const ChatScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/elder/today',
                    builder: (_, __) => const TodayScreen()),
              ],
            ),
          ],
        ),

        // S5–S8 照護者模式：底部 4 tab
        StatefulShellRoute.indexedStack(
          builder: (_, __, shell) => CaregiverShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              navigatorKey: _careNavKey,
              routes: [
                GoRoute(
                    path: '/care/summary',
                    builder: (_, __) => const SummariesScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/care/timeline',
                    builder: (_, __) => const TimelineScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/care/stats',
                    builder: (_, __) => const StatsScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/care/manage',
                    builder: (_, __) => const EldersScreen()),
              ],
            ),
          ],
        ),
      ],
    );
