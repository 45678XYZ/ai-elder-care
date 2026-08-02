/// 密碼規則，對齊 terraform/cognito.tf 的 password_policy：
/// 至少 8 碼、要有小寫字母與數字（不要求大寫與符號）。
bool isPasswordValid(String password) =>
    password.length >= 8 &&
    password.contains(RegExp(r'[a-z]')) &&
    password.contains(RegExp(r'[0-9]'));
