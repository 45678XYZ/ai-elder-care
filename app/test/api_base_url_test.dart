import 'dart:convert';

import 'package:e_hakka_care/shared/services/api_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// `--dart-define=API_BASE_URL` 帶不帶 `/v1`，組出來的網址都必須只有一層 `/v1`。
///
/// 這條踩過一次，而且症狀完全不像路徑問題：`terraform output api_base_url` 給的是
/// stage 的 invoke URL，stage 名稱就叫 `v1`（端點全掛在 root），所以那個值**本身就以
/// `/v1` 結尾**。client 若再加一層，打到的是不存在的資源，API Gateway 找不到匹配的
/// method 就退回用 SigV4 去解 `Authorization`——而我們送的是 `Bearer <JWT>`，
/// 於是每一頁都變成 `Invalid key=value pair (missing equal-sign) in Authorization
/// header`，看起來像登入或後端掛了。
///
/// 本機 RAG PoC（`http://10.0.2.2:8000`）沒有 stage 的概念，所以兩種寫法都得支援。
void main() {
  /// 攔下請求、記下網址，回一份最小的合法 `GET /elders` 回應。
  (List<Uri>, http.Client) spy() {
    final seen = <Uri>[];
    final client = MockClient((req) async {
      seen.add(req.url);
      return http.Response(
        jsonEncode({'items': <dynamic>[]}),
        200,
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    });
    return (seen, client);
  }

  Future<Uri> urlFor(String baseUrl) async {
    final (seen, client) = spy();
    await ApiClient(baseUrl: baseUrl, httpClient: client).getElders();
    return seen.single;
  }

  test('base 不含 /v1（本機 PoC 風格）會補上一層', () async {
    final uri = await urlFor('http://10.0.2.2:8000');
    expect(uri.toString(), 'http://10.0.2.2:8000/v1/elders');
  });

  test('base 已含 /v1（terraform invoke URL）不再重複補', () async {
    final uri =
        await urlFor('https://abc123.execute-api.us-west-2.amazonaws.com/v1');
    expect(uri.toString(),
        'https://abc123.execute-api.us-west-2.amazonaws.com/v1/elders');
    expect('/v1'.allMatches(uri.path).length, 1,
        reason: '多一層 /v1 會讓 API Gateway 退回 SigV4 去解 Bearer token');
  });

  test('尾端斜線不會組出雙斜線', () async {
    final uri =
        await urlFor('https://abc123.execute-api.us-west-2.amazonaws.com/v1/');
    expect(uri.toString(),
        'https://abc123.execute-api.us-west-2.amazonaws.com/v1/elders');
  });

  test('尾端斜線且不含 /v1 也只補一層', () async {
    final uri = await urlFor('http://10.0.2.2:8000/');
    expect(uri.toString(), 'http://10.0.2.2:8000/v1/elders');
  });
}
