# 部署拓扑与加密组网方案（分阶段演进）

适用：行业协会政策法规知识库平台。数据主权在协会，推理与用户接入分阶段从「办公 Mac」演进到「IDC 买断服务器」再到「云 GPU 弹性扩展」，各节点间全程加密、可信组网。

---

## 一、三阶段演进总览

| 阶段 | 时间 | 节点与位置 | 职责 | 触发条件 |
|---|---|---|---|---|
| **1 开发验证** | 0–6 月 | 64GB Mac mini（协会办公场所） | 原型开发、知识库采编、管理后台；可跑 Ollama 做轻量推理验证 | 立项启动 |
| **2 生产上线** | 验证稳定后 | 买断 GPU 服务器放 **IDC 机房**（托管） | 全部生产栈：推理(vLLM 32B)+用户接入(nginx/Dify)+向量库/元数据 | 原型验证通过 |
| **3 弹性扩展** | 大规模/需负载均衡时 | IDC 服务器 + 租用 **GPU 裸金属云服务器** | 推理集群负载均衡；云 GPU 经加密隧道加入 | 并发/规模超 IDC 单机上限 |

核心思想：**重资产投入随验证进度滚动释放**，阶段 1 不押服务器，阶段 3 才按需租云。

---

## 二、各阶段组件分布

**阶段 1（Mac mini，办公场所）**
```
Mac mini 64GB
 ├─ Dify (开发/管理端)
 ├─ Ollama + Qwen2.5-32B (轻量原型推理)
 ├─ Qdrant (小样本向量库)
 └─ 采编工具 / 知识库管理
```

**阶段 2（IDC GPU 服务器，买断+托管）**
```
IDC 服务器 (48GB GPU)
 ├─ nginx (HTTPS + 限流 + 负载)
 ├─ Dify (生产门户/API)
 ├─ vLLM (Qwen2.5-32B 推理)
 ├─ Qdrant + PG/ES (向量 + 元数据/BM25)
 └─ Redis (语义缓存)
```

**阶段 3（+ 云 GPU 裸金属，租用）**
```
用户 → IDC nginx ─┬─ vLLM@IDC (主)
                  └─ vLLM@云GPU (加密隧道, 弹性副本, 负载均衡)
```

---

## 三、网络拓扑（逻辑）

```
[会员浏览器/微信]
      │ HTTPS (公网, 仅 IDC 暴露 443)
      ▼
[IDC 服务器] nginx ── 内网 ── Dify ── Qdrant/PG/ES
      │                         │
      │ 加密隧道 (WireGuard/IPsec/mTLS)
      ├──[Mac mini 办公]  (采编/管理, 经隧道访问, 不暴露公网)
      │
      └──[云 GPU 裸金属] (阶段3 推理副本, 仅接收加密后的上下文)
```

- **公网暴露面最小**：仅 IDC 的 443 对用户开放；Mac 与云 GPU 仅通过加密隧道互通，不直连公网。
- **云 GPU 不存知识库**：只接收「问题+召回片段」做推理，结果回传，原始库留在 IDC。

---

## 四、加密组网方案

| 链路 | 推荐技术 | 说明 |
|---|---|---|
| Mac ↔ IDC | **Tailscale**（基于 WireGuard，免公网 IP 配置）或自建 WireGuard | 办公机动态 IP 也能稳定连；先 Tailscale 跑通，后期换 IPsec 提 SLA |
| IDC ↔ 云 GPU | 云厂商 **VPN Gateway（IPsec）** 或 WireGuard 站点隧道 | 组成同一私有网段，vLLM 副本互相可达 |
| 业务流量 | **HTTPS / mTLS** | nginx→vLLM 已 TLS；叠加隧道双层加密 |

### 4.1 Tailscale 快速组网（Mac ↔ IDC）
```bash
# 各节点安装 tailscale，登录同一组织
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# 得到如 100.64.0.10(Mac) / 100.64.0.20(IDC) 的 Mesh IP，直接互访
```

### 4.2 WireGuard 简化配置（IDC 侧片段）
```ini
[Interface]
Address = 10.10.0.20/24
PrivateKey = <IDC私钥>
ListenPort = 51820

[Peer]
PublicKey = <Mac公钥>
AllowedIPs = 10.10.0.10/32
# 云 GPU 副本作为另一 [Peer] 加入即可
```

### 4.3 nginx mTLS（IDC 内部服务间，可选加固）
```nginx
listen 8000 ssl;
ssl_certificate     /etc/nginx/certs/vllm.crt;
ssl_certificate_key /etc/nginx/certs/vllm.key;
ssl_client_certificate /etc/nginx/certs/ca.crt;   # 仅信任内部 CA
ssl_verify_client on;                            # 强制客户端证书
```

---

## 五、IP 规划示例

| 节点 | 隧道 IP（Tailscale/WireGuard） | 角色 |
|---|---|---|
| Mac mini | 10.10.0.10 | 采编/管理 |
| IDC 服务器 | 10.10.0.20 | 生产主节点 |
| 云 GPU（阶段3） | 10.10.0.30 | 推理副本 |

业务域名：`knowledge.example.org` → IDC 公网 IP:443。

---

## 六、数据流向与合规边界

| 数据 | 存储位置 | 是否出协会控制域 | 约束 |
|---|---|---|---|
| 知识库原始文档 | IDC（生产）/ Mac（采编） | 否 | 仅经加密隧道在节点间同步 |
| 检索索引/向量 | IDC | 否 | 同知识库 |
| 推理上下文（问题+召回片段） | 阶段3 临时传云 GPU | **是（仅阶段3）** | 云侧不持久化，签数据处理协议 |
| 用户问答日志 | IDC | 否 | 审计留痕，权限受控 |

**合规红线**：若协会章程要求「数据物理不出协会」，则**阶段 3 不能上云**，只能继续在 IDC 加买服务器。立项时请确认允许的尺度。

---

## 七、运维要点
- 隧道密钥定期轮换；IDC 与云间用云厂商安全组/白名单收窄到隧道端口。
- 监控隧道连通性与延迟；云 GPU 仅在负载阈值触发时拉起，闲时释放省成本。
- 所有节点时间同步（NTP），日志统一归集到 IDC 做审计。
