import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../widgets/form_widgets.dart';

/// `/auth/consent` — 使用者同意機制與資料保留政策說明。
///
/// 從註冊頁的「查看說明」連結進來，純資訊頁，看完按返回即可——不在這裡放同意勾選，
/// 勾選動作留在註冊頁本身（[ConsentCheckbox]），這頁只負責把內容講清楚。
///
/// 內文走長者規格（24sp 下限）：註冊頁在登入前還不知道對面是長輩還是家人，
/// 一律照長者做（見 sign_up_screen.dart 的說明）。
class ConsentPolicyScreen extends StatelessWidget {
  const ConsentPolicyScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final sectionTitle = text.headlineSmall;
    final body = text.headlineSmall?.copyWith(
      fontWeight: FontWeight.w500,
      color: AppColors.inkSecondary,
    );

    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: AppSpacing.pageBody,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              BigBackButton(onTap: () => context.pop()),
              const SizedBox(height: AppSpacing.xl),
              Text('使用者同意機制與資料保留政策', style: text.headlineLarge),
              const SizedBox(height: AppSpacing.xl),
              Text('這個 App 會用你的資料做什麼', style: sectionTitle),
              const SizedBox(height: AppSpacing.sm),
              Text(
                '註冊後建立的帳號與資料，用於這個 App 的核心功能：語音陪伴對話、'
                '每日健康摘要、用藥與行程提醒，以及照護者查看長輩的動態。\n\n'
                '對話內容與你提到的健康相關資訊會被記錄下來，用來產生摘要與提醒，'
                '並在你或照護者需要查詢時使用。若你用語音互動，裝置會先把語音轉成文字；'
                '客語目前無法在裝置端辨識，會把錄音上傳到後端辨識後即刪除音檔。',
                style: body,
              ),
              const SizedBox(height: AppSpacing.xl),
              Text('健康資訊僅供參考', style: sectionTitle),
              const SizedBox(height: AppSpacing.sm),
              Text(
                '本 App 僅供生活陪伴與健康資訊參考，非提供醫療診斷之參酌，'
                '不得作為醫療診斷、治療建議，或取代專業醫療人員判斷之依據。'
                '如有身體不適或用藥疑問，請務必諮詢醫師或藥師。',
                style: body,
              ),
              const SizedBox(height: AppSpacing.xl),
              Text('資料怎麼保存', style: sectionTitle),
              const SizedBox(height: AppSpacing.sm),
              Text(
                '帳號認證使用 Amazon Cognito，密碼不會以明碼保存。'
                '資料傳輸全程加密，儲存於雲端資料庫時同樣採取加密保護。\n\n'
                '對話紀錄、健康事件、每日摘要與例行公事會保存在雲端，供 App 核心功能使用；'
                '語音回覆的音檔僅提供短時間的下載連結，逾時即自動失效並清除。',
                style: body,
              ),
              const SizedBox(height: AppSpacing.xl),
              Text('要求刪除資料', style: sectionTitle),
              const SizedBox(height: AppSpacing.sm),
              Text(
                '想刪除帳號或所有相關資料，可以聯繫照顧你的家人或系統管理者代為處理。',
                style: body,
              ),
              const SizedBox(height: AppSpacing.xl),
              Center(
                child: TextLink(label: '知道了，回上一頁', onTap: () => context.pop()),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
