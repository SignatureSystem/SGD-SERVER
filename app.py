import os
import json
import uuid
import pytz
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)

# ─── CORS — allow Chrome extension requests ───────────────────────────────────
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Token"
    return response

@app.route("/validate", methods=["OPTIONS"])
@app.route("/cookies", methods=["OPTIONS"])
def handle_options():
    return jsonify({}), 200

# ─── Config ───────────────────────────────────────────────────────────────────
ADMIN_TOKEN = os.environ.get("SGD_ADMIN_TOKEN", "changeme-admin-token")
DATA_FILE = "/tmp/sgd_licenses.json"
SL_TZ = pytz.timezone("Asia/Colombo")

# ─── SGD Cookies (update via !sgd setcookies) ─────────────────────────────────
SGD_COOKIES = [
    {"domain": "chatgpt.com", "name": "__cflb", "value": "0H28vzvP5FJafnkHxj4bfehZKNMupZgWGPyC3Jor89h", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "oai-hlib", "value": "true", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "_dd_s", "value": "aid=3e49fb69-a0a6-4721-8104-536142453465&rum=0&expire=1781289831843&logs=1&id=9e099f62-dea2-44b3-81bf-6276952a6d89&created=1781288521580", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "__cf_bm", "value": "MEGp7e.nHIG_kaQrmq87yRcmkdxfX1Q.dSf5t1Mg4Sw-1781288773.0458825-1.0.1.1-hLexHPYEVp851bGkF6XP13b2_gwUatCyzdAfc5Ljo_GdkvWmJ6j8uObx_IeCq1SmuHZTimUVaPMmJxZFCui32vRqjafuUL0O8PvDwFuAfky7BobDF9O6WboZwKytotvD", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "_cfuvid", "value": "mT_QMd51moei1ST5MSRj0IRFDLU.kGKf2z1HgLFG1RE-1781288773.0458825-1.0.1.1-oe.QRQETJFcHmmxfiILju9f58Yz1A1YyRfHcY4ZHPq4", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "oai-sc", "value": "0gAAAAABqLE9GYzJLjZtk0gty7KptlIVg8T5DUQmgGVk37ctQxDeR0k0Qh_9dfUpcQx_zKWkT0-5mSkFXdoWgku1Tk9Ka9dcbuTahOumIublmWhKAxn9uANLjhfwHtdASfS9I5EONtXt3I7gzZBFbkLTYgARcAZyQe7kkwTyIdQG-EaC8nNS6evUmU1nSqdO_awYEE_sjn6nSlbieO6irsHiJD9jRhA8SK02qPTF8n_h0PH4grk2jCZk", "path": "/", "secure": True, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "oai-gn", "value": "Ahmed", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "__Secure-next-auth.session-token.1", "value": "C3gUFA5NFgvCOqtVl4ciyt-NNeGWcWOROke-_7Wb-2wFjTnl9l3obJwvDrFMmRhM1CaTWmG5mSUDHqBMJpKaovVli_UH7Z5rhSfwyNXmO8mR_8AtUgnfAWRW9pOlZW4yJVWVhXMPR8Ld5AIT1naipa3RW21v6d7BQbrk7TDj0E0sYcsJt7N1eWCU1t9Ca9kFTiljvcPUEfk4cwdyvu6Kub5FvQA45yjMLvKdgPfLjvKBuZfanOQkiHOVLXHc9gaBIPlomIm2Ce9REwqnyt5nnQBYQcTJEeUbJxy6kw9veWSlIfpGNzjmaqdNsV6G0-0j7Knr0a1kLIjvaKs7q-G6y-MOAOAI7qNbBpQh8bBK_mbDv3kWtoVTd46osaDR5PIkZ4_Pg_ZLaRDjUShg5xmsGse4yd6eJrR3lWP-gIyWZmXOZ4iqFRk4FeMZ7Vy_TO-Lqp5IRYJ9-d00v0_wLdRXjQ827y6U8_fSvKlhpOGprSA6Z7_Sf4m6rGdIKMmuF-04qgMxXp3C5FYkGbqRFCI0G2fMqjRyr2Sei_VMsS7Lr4Te7bdzNp0uhYxs2wJbldifO29Rcz92XtHkp39uK.wtIazw8wwP7Yacq0YnTv4Q", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "_uasid", "value": '"Z0FBQUFBQnFMRTZqdUNFVjdlNGp1M1BjM1AwcV9aeXpjSl9qVl9ZclJVd0FwNi1VZUI3RlFNNWQ2ZS1KY2FZTV9WOG9YaDh4c3ZWSVlCbXBjMmt2V2duc3Q5a1QwNGhVaWhuWlZvVVJDamdkcFlaVEtPUFpva09qNHVSNHdOV0pmS3pSY3l1WkVCZ2JwWTN2c2wwbjJjMVpYVXVYT1NPcGdqQjExZ3ExU3lVZ19uMkZZT0FvQlZ3aWZFbmNHRkQwWml6WTV4MWZ3TDc3cU9xZ1pkN05BY2FWQVVLemdmeThabVhlcHE0X2RmMEpCV1RjZzFnX2RtQ3JNRC1mUVBLRDN2MDNzcldzSXh4Z3dIbkFmSHdzYlV3bTBQOU1ITG1UbDhjMXI0QTRJX2Fjb0xSOTJMTFJpMWtwbEdOdW1CbFdSc2hYR3ppTVl6MHZUUWtPODNJRXlHN3JtSEhmUEhZbzFnPT0="', "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "__Host-next-auth.csrf-token", "value": "331136718f7f3c7a615c95b342b36dfcfb2e37399eb894c23d14777e06427f4d%7C6ab0216eaace6655a56f619e5365543ffe9a7de4b0aa9354233b7266e50cd22c", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "__Secure-next-auth.callback-url", "value": "https%3A%2F%2Fchatgpt.com%2F", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "__Secure-next-auth.session-token.0", "value": "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..b88Cc9wwayK2xovJ.ubXUnR43JsyVYO6_nsHR90iDBgA4W7TFmI6Q-Z7rCylJgcXoAIZrNhXggFPYsz3lr0uNOpATCtxvkNzzB-KRTnJku39iZBcHiIV9vKFkA_vmH0yHEBYXz5ab9EnJLJCUQULKLWIkOHOWAwLLPUr9jLKqH6rAUOykbENKzaZVaH6uGCJp52iAbvY4qOdecukRxR_iajs4fAHHhuxMtF2TMST_7t3bWYuc-x1uN6pHjDCZ5vCKltKr_jlDvpf3J_FaowTjPDe1HYKGN-Fs3yLbTR6IdQkEs5XDPcoydrQ5qtJ9cNj5T03454m9OdafgXZAWlex8Na81cdJ93qmR7BjBtzd88srSwGShyKMhATS1wMl1aL-9m-qhQ9uYyBxJPwVk47aKRrk1H08HBz2xE2vfisHkGWjXq6bFfMDsNKoWKqwyiAKvMGqKOM9ICxcQmjYoDGewZA6Liz3WrsB6VC9-6ZwC_hBOg9Pjio5G0ED4Be9c93YF6FiSwPgPSSOdShL56Dt7Og-Y8AmxXoX529d7f9O70wcjDlUHpwj-nwuEEqkgxRw14tbxFY2rA-RZ9RbAwnMiM41nAizTIs1H8fSWFQ9AP2SJ7y4sYsCDvQXLTKVLdPR9UyPucI-9Xm2CaGq1SQU3UHBr66fLr--pwWPJttouJ0TI_uDiWnl1Lz10Mhj0XxeO7VE50N4G9c8GpfM-jbltUQmns0XqlmGNCQhEmXyzWO0MU1MZLS-7k0wg9JZenemeUA9GS9ugfddcoRa7J_1XEKjssPY1JsnohFDYUL-OKzavCqJFzMFt0jK98EvfC3qxj07_-aLEQoiBv7Rocdgq4ysWKwK_5b-7DgR72b18huMT_a_uG_BSmyorsI0oEXl5xnNaZi2ouetRir3U_eMv67lmi0ebGIFdB5AP4Zf4Ps_n9oTeCVNhUrk5_gHgF7FidJsVJZG3ei8aFZF0QkaNmeh2vm6gqeUMTG9A8bQQXHWN8HYxYnaCnJT_UWXKNXsxEc-FM6Zur7skPAA7OZ9zq0dEOj1GwfTJ2l1cx5K7bimr40CdFE_FrsmX4JE4GGD9zqnmdfM-tNXNcecXplwq55JoeIJPbgI3qJSOEtvLw9SvJbeyjV6tYpTex7_iz39AbKI44LWlf7HkRhxBI8NQINbdB1lYO1wFGAcSxHXwWvFHmt5APsssbHvaGSV1ugAwG8-kDRsYPZ6BP3yTde3p5cBeNo7hrbYaHJ0AjPHS1Mgis-zL2l1bWIq7FO1Pdh0SvVr_tDyyuUvd6NXlgDUTjtby0GFDkwe_jdsmNs9qziTON4p61dP3k07j6_RbvGbeZxrre0LZaV0QMhr38ew0pxcj6KKB7BfbRSLVW_-7XZVErxjvL15hi9BTefRtyTY8Id7x6-6mVWW_ep5zVruFs8xlLTBqkPxCcPz9QHfxEai4oJzl3vGWM9Ee43w-CkynNPcWbeSe2AReyqG4MLfCrwpuDrVFvDpHmdsEtOlIvz-PWNkeXH4NG0zds0oHCdZanV_zASxOpUGIFURh1qiLIiU-F6unxCDC0b5MFOK99ZAx9wgoaJxcbZZKbFc3qB8gUKPvS79lgkkijKhUgiKm33TeV5BPyKbI6RNrxzfHTY5B-4G_I6ISCx4lE8uuInpw7XanUiefM9BYdcqOqowou63eEUXn8FLfgTX5xKu_hGsC5nMw0gWqLw_JRvBOT5stsJtAQJixlYieJFlMevtTUV4f17faTH7RJsSEDc41rKL42mZe7VhvdFFVA94S3OHHZuEiI_Rr2ft0QkWpdK0VJE6B5ghslLeHMLMIgmED0xmFm6KW1F0ClJ-j35Bk5xHz9e5Yv1WIkFwB1staStvHKG7zui0ht3Ib0cTvHe7IuGg5d99rUgXgJVLF5ruqS4e1EU3x_l666opIooMSzrx1iKeP8dkNTGwbTKon_ytCOxfgwgXYdAUy52FVywlB7tIDkZakkqURM-NFp7xBrsdNTBORPyynBSSjlWGonubkGx9fuBV72bR_TYqpz5iJhEeX31OIWydoFaaedM1jJoIJehXHnGDgPdEzK89dbglz0faTqJaZ56-fSKc4TNQKdAlzWzMRMIPP8N3f9UGX_Nivs0XSZUY2FL0LXfDV30v4LTjbkEOHtsQEHu0w96EKainOLKCrDuUZoLBVdTy3IA6eoRqBh39UxeYIa3XcCHb3hJcmbrK3TEUZStcf9tTMYbIvGkUSRHosVSC20aGquF4mUswIhWKJkyl3JH_bwDYCixhhT3OkWWzKXypwYbLsNUU6J0LicPopamra3Lq49fKg3nXfpgdOd-l7vGkpR5Ip2u7i1wRHD2slPORQfQDs3Qu_9a7HQfg7TKjanAD1LwSMo8EcW2Q2J4UtS-Mh3sUL_Rma5dLUrNtBVXxKv4fVtpKWTLroC16VYniYXcP1usw3WgSauTEIkDi8gn0PsJxOcfE15BRO7noo5cTG7cvE9z5YijoPRMTtGdXHPgRH9mYqcWTVTZe1ynjxx_aAz7WojdIbLD4pkNcNyVZrC8FjTngucn4CCbAGHC7h9POqKxXnmJVsb769IuW7biEbS9Bv4zQ4ja2kFZgRs8WDwlbQgC0VrAyUFobMwycBqG0JPojvK6c3CB315SWiNy0dNBiaKW2gUnifqBT5U1hkljTqnGsMxzfAJjW6Rw8409OAXRKnKYXOpW6KaBUGzwIl-N_6uAJaQmunyE7jdPUcHfURwYF1eL190JSvqlxH-caJjO1oSlRGt2N-GBSqEhemQj9tXoovwGT2ud7ZAobSmDMGtJ2DBC0h5eUoj-pecTOHki6ihZHtGp-a8Ey27xrrLGwkDH4Kqur6Lxhl7QnO2uNwl04E5bGEuXEVl-Dlz4AWzzESsWNerA66usr0G24HVc0KUOELPgSiVnEI_xDK59YBFEnXrYfL-iMrR9oiFHwtqGZZ4hGSvG1ASR94me6kWeqc4BLQza1AnuvU249QEjT9NFH9XbG4-Ts9LDuqOFmfuqrzJz1u5JmSuQtIFYByHMnUay9cQVS-PQL_0ov7XgOfx9vIjPt61Fvdj67aCz_XOCWpNUMMAkl8S1VRFHNNBQ6wIuk1qi-FQvaJSdaCffeh0fkLQpn_BxupX96P9b2VFghjVq8qxGgh7QMAQ8nWWadMMF5cNeYmPkMeSQ_OVqiW3fumdrY_hOh6gF0-troQZH9KEF8VOyHknFRIdBAVlMsNIKNdmszYJBat7SQx2Cfjyr8INH6Qx6W7wZtopCf3RsJsdzJaEoLsHglw-q9dxmFimEqIQqL2v1u5TTiYjMC6ULN4tNDNP56ViL_jQsbAT6uiQ5Snz8jVvMkj9levT3XfGXrCSkphtKqzIOacO5ioD7Gwi8j1TpQznvPWbIWB8a22525d_06l7BdYGm7voINzt_TD7LEJA_aA29ZiQxR0kMm3NlHF4MQU8xjPRlbu94Us5aS-InTEKqmAZQgHoHxpZ06fOZcuk5Cw4AfjqYz2ZmLGoVB7bSpkf4ExDDZAOZPsDqxd_D7HnrgZ6tEWX_oitVim1aFWiunDa8HjXx6O24GI_QeSIsd4ZG8A5sVw5-SZpDZVbRB7OHE5mMHBI8rxSo8V4BCQNVYwvTYaA2Ao-t9tsBZfK-VxAVVCWdHmFpt9p6V_oaUXAw0yiwmv3nxJGQQNz-snW3yXrGTrBRzoZ8QLJG6Q59n9oegNX_YwBEgWr9QuWDFYjwXOOn1baoD6kaYY176PJHCYTV8RNd_YfU_qv1t8M_U3omnF4HHFN835u4wW96Aa0ULHOGL5Pw58aoocCpC16tH9hNTJQLFNHW7GvYo_m6Lg-pMgescVEyDlLJMVxw3PF2BkAxqGtAqlOvSAu8fENe", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "__Secure-oai-is", "value": "ois1.eyJ2IjoxLCJhbGciOiJBMjU2R0NNIiwia2lkIjoiY2hhdGdwdC13ZWItdjEifQ.BoXyEmItRDQXA-Gk.HO3oUzP2dP_tZrmrIFhzCCnhDLkJsebDchicpe1LFv5rdmBZVzzSSY56uqQui2Qs9HBUAt2RYyjN2HcvK65wKBU2dXMiZfAeDvvWZDxnwQQagR5hbtNo-jH56hvuzw6H-_K_CeXwrOEmsTvJZEu_q7vT6FkUKTs_eRGmlnndDBjXaRHMerRPgT2sGmyR7d0WmrZPOCXoxLrnQdgWb1My6iGnf9ClNwcMNYoCQ217FX0gjN2uBL9JHwVbfeiJQhel1ZdUUfQwUWHv8amoh3-IdwF0TV2qp-coWWs1SCtz5137KNQKS6ecrbqM3p98-lhjRCtlyxndVqDIKpm1AtTxBgYb86LudHjgIunDAkV5Y0aZq-bx_EZML8m-ubYy9xN3X96gSphBcg_Ec53GGFGi3CVx2wJJz8jYBFs635dWGZtJEBFSVg-GBey9MfbZae07d2DB0oqWaC6eE36_VP8xaAfkOaWpHU7qiFrUi9kQ6QkRtFQoBn9szKbu5WHhj6bX-gGkQH-5G3EebKIl8wMiaP1zNwhd4P42tE0Tf9WDQpMo0eHyrWMp-PBJiMJi6kSeqAEW6E7foOWXBxmsdyRX2LHI0XmWnhJEHdsk6IZN_hsG3QJdgJfjVo5Cd-SVnthaGIXp8L1F4cmfXfZh-Ywqo3gTs1B5RvNUaTCUjjUSNDgcIqyH7wtrDVYforWqBmSsx57dF20ji5YcRvuJgZgcZh9BCWpDApGCUwgUPfP4gTJqAo_tslHz34d0Ee4QFQeWLsCMtpZUDvZNaQl520Cguw_KSbYX7XnSfDXNtgHi-G2s0EIcznrWuyZE3D81kwq_pOavX67_m6iP6NUCpS2WIt37W7cxuQMZHwzOS6W0mX8XM7jiVB1cPeG--GHZQd2UtHPRaEC85WNk0IJEVoU8nssIHXRrMR6uVN7B0mgp2wKhxb6tGDH92oIo2UP5QOaQHmTwm8CQSUWLAd1H3Izm-eGI0VXsHc8qry8UcBjv3dRQfbYehxckZbnWAnMqvXC70InDcma41hEPoLGw-TGkL_Gc2Wvlj_PhXi8u9rVKo7CAjZDVnZQBQlX-8L5uWfZDR9eOIFud2IniEe9Rc7tln3uqJAS08FuYArjKMIQktlu6qf_K5XztKNOy26t3pTrziD31PsWWYDjy2d4tRYU3SKAQ3keeOpQ8uB6trHeqq6SvFPxAaei8HnE23p5CFoHiG086x9DwTnePmKWQvYrrLCqTv_cU0bsPfyOBDSPNV7Pu4VFzgwwcBEMWZqR-8_LOZYbguklT2twwbnwmn99DJnZnHeIqz7QCAW2d607lFHRAgBIB_o17jAbcC577_wHjx36ZPFJQjS1UfocmzSrzIl169lY8lcFRZTprDMu-Qy_TUwQ-nS5_i83bvEbi3EFCbn8rHG1ipOAOLI3wEJzkUnUIsXunBUGZyHKLSpZEhEIQDFak-QWa4cxVxD_oCng9SpSEr-b-KpYwXQlZPR9yabwcL5fzc3nw9wktutyKtYWhFODPKOKOJNI8bi45xbaHcRtm", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "_account_is_fedramp", "value": "false", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "_umsid", "value": '"Z0FBQUFBQnFMRTZqX2xhM2sybF9hRUJnMEF0UmV5QmxaTFdydW90d2xvQWpLQ1ZqTFZ2R1M2dnpCeUlTaGhGMW4wbl9pakV6V3R2UkthbTZFTXQwa253R0tTdFowQ1pSd2l4TklLMEkzLVZjX2R2WVM1ODJ5RERxWlNzSVZhZnBla2FvT3FhOFJkYmVORzJkQnJ6V2ZLTW4tVEVjTXU1cnk0bXNhQVBuM0tKNnFXdFp6Vkk0dVhNTkFPaFRNV0xZNTlNQkVMTTVkd3hCMjFHaUg3VzhIX0JNRHllWGdPUnQwTEFtWXdtX2U4clU3TmlfWExudHFPST0="', "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "g_state", "value": '{"i_l":0,"i_ll":1780709011665,"i_e":{"enable_itp_optimization":0},"i_et":1780709011662}', "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "oai-chat-web-route", "value": '"ChMxMC4xMjkuMTY3LjEyNDozMDAwEKqGWQ=="', "path": "/", "secure": False, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "chatgpt.com", "name": "oai-client-auth-info", "value": "%7B%22isOptedOut%22%3Afalse%2C%22loggedInWithGoogleOneTap%22%3Atrue%2C%22user%22%3A%7B%22name%22%3A%22Ahmed%20Bin%20Fawser%22%2C%22email%22%3A%22ahmedfawser%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Faf.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1781288775002%7D%7D", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": ".chatgpt.com", "name": "oai-did", "value": "59d074a4-6c84-493c-8c21-eab44a44da10", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342}
]

# ─── JSON Storage ─────────────────────────────────────────────────────────────
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"licenses": {}, "cookies": SGD_COOKIES}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def sl_now():
    return datetime.now(SL_TZ)

def sl_isoformat(dt):
    return dt.isoformat()

# ─── Admin Auth Decorator ─────────────────────────────────────────────────────
def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Admin-Token")
        if token != ADMIN_TOKEN:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "SGD Access Server running"})

# Validate license key (called by extension)
@app.route("/validate", methods=["POST"])
def validate():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    device_id = body.get("device_id", "").strip()

    if not license_key or not device_id:
        return jsonify({"success": False, "error": "Missing license_key or device_id"})

    data = load_data()
    license = data["licenses"].get(license_key)

    if not license:
        return jsonify({"success": False, "error": "Invalid license key"})

    if license.get("status") != "active":
        return jsonify({"success": False, "error": "License is revoked"})

    # Check expiry
    expiry = datetime.fromisoformat(license["expiry"])
    if sl_now() > expiry:
        data["licenses"][license_key]["status"] = "expired"
        save_data(data)
        return jsonify({"success": False, "error": "License has expired"})

    # Device lock
    if license.get("device_id"):
        if license["device_id"] != device_id:
            return jsonify({"success": False, "error": "Device mismatch. Contact support to reset."})
    else:
        # First use — bind device
        data["licenses"][license_key]["device_id"] = device_id
        save_data(data)

    return jsonify({
        "success": True,
        "expiry": license["expiry"],
        "phone": license.get("phone", ""),
    })

# Serve cookies (called by extension after validation)
@app.route("/cookies", methods=["POST"])
def get_cookies():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    device_id = body.get("device_id", "").strip()

    if not license_key or not device_id:
        return jsonify({"success": False, "error": "Missing fields"})

    data = load_data()
    license = data["licenses"].get(license_key)

    if not license or license.get("status") != "active":
        return jsonify({"success": False, "error": "Invalid or inactive license"})

    # Verify device
    if license.get("device_id") and license["device_id"] != device_id:
        return jsonify({"success": False, "error": "Device mismatch"})

    return jsonify({
        "success": True,
        "cookies": data.get("cookies", SGD_COOKIES)
    })

# ─── Admin Routes ─────────────────────────────────────────────────────────────

@app.route("/admin/add", methods=["POST"])
@require_admin
def admin_add():
    body = request.get_json()
    license_key = str(body.get("license_key", "")).strip().upper()
    phone = body.get("phone", "").strip()
    days = int(body.get("days", 30))

    if not license_key:
        return jsonify({"success": False, "error": "license_key required"})

    expiry = sl_now() + timedelta(days=days)

    data = load_data()

    # Check if key already exists
    if license_key in data["licenses"] and data["licenses"][license_key].get("status") == "active":
        return jsonify({"success": False, "error": f"Key {license_key} already exists and is active. Use !sgd extend to add days."})

    data["licenses"][license_key] = {
        "phone": phone,
        "status": "active",
        "expiry": sl_isoformat(expiry),
        "device_id": None,
        "created": sl_isoformat(sl_now()),
    }
    save_data(data)

    return jsonify({"success": True, "license_key": license_key, "expiry": sl_isoformat(expiry)})

@app.route("/admin/revoke", methods=["POST"])
@require_admin
def admin_revoke():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    data = load_data()

    if license_key not in data["licenses"]:
        return jsonify({"success": False, "error": "License not found"})

    data["licenses"][license_key]["status"] = "revoked"
    save_data(data)
    return jsonify({"success": True, "message": f"{license_key} revoked"})

@app.route("/admin/extend", methods=["POST"])
@require_admin
def admin_extend():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    days = int(body.get("days", 30))
    data = load_data()

    if license_key not in data["licenses"]:
        return jsonify({"success": False, "error": "License not found"})

    current_expiry = datetime.fromisoformat(data["licenses"][license_key]["expiry"])
    base = max(current_expiry, sl_now())
    new_expiry = base + timedelta(days=days)
    data["licenses"][license_key]["expiry"] = sl_isoformat(new_expiry)
    data["licenses"][license_key]["status"] = "active"
    save_data(data)

    return jsonify({"success": True, "new_expiry": sl_isoformat(new_expiry)})

@app.route("/admin/check", methods=["POST"])
@require_admin
def admin_check():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    data = load_data()

    license = data["licenses"].get(license_key)
    if not license:
        return jsonify({"success": False, "error": "Not found"})

    return jsonify({"success": True, "license": license})

@app.route("/admin/list", methods=["GET"])
@require_admin
def admin_list():
    data = load_data()
    licenses = []
    for key, val in data["licenses"].items():
        licenses.append({**val, "license_key": key})
    licenses.sort(key=lambda x: x.get("created", ""), reverse=True)
    return jsonify({"success": True, "count": len(licenses), "licenses": licenses})

@app.route("/admin/resetdevice", methods=["POST"])
@require_admin
def admin_reset_device():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    data = load_data()

    if license_key not in data["licenses"]:
        return jsonify({"success": False, "error": "License not found"})

    data["licenses"][license_key]["device_id"] = None
    save_data(data)
    return jsonify({"success": True, "message": f"Device reset for {license_key}"})

@app.route("/admin/setcookies", methods=["POST"])
@require_admin
def admin_set_cookies():
    body = request.get_json()
    cookies = body.get("cookies")
    if not cookies or not isinstance(cookies, list):
        return jsonify({"success": False, "error": "cookies must be a list"})

    data = load_data()
    data["cookies"] = cookies
    save_data(data)
    return jsonify({"success": True, "message": f"Updated {len(cookies)} cookies"})

@app.route("/admin/getcookies", methods=["GET"])
@require_admin
def admin_get_cookies():
    data = load_data()
    cookies = data.get("cookies", SGD_COOKIES)
    return jsonify({"success": True, "count": len(cookies), "cookies": cookies})

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
