/*
 * ==============================================================================
 * Project: Aegis-Volt Pre-Emptive Cross-Layer Network Orchestrator
 * Component: aegis_volt_xdp.c (eBPF Data Plane: Ingress XDP + Egress TC ECN)
 * License: Open Source (GPL v2)
 *
 * Architecture Specification Alignment:
 *   [Ingress] eXpress Data Path (XDP Hook)
 *             -> Zero-copy packet interception before sk_buff allocation (< 10 us).
 *             -> Microsecond inter-arrival Delta t & 8-frame sliding window quantiles.
 *             -> Intercepts out-of-band Layer-2 NILM pre-emptive energy frames (0x88B5).
 *             -> Streams 32-byte zero-copy metadata via lockless BPF Ring Buffer.
 *   [Egress]  eBPF TC Egress Modifier (Active TCP ECN Injection)
 *             -> Intercepts bulk congestion streams at TC layer.
 *             -> Injects RFC 3168 ECN (CE = 0x03) bits directly into IP TOS/DiffServ.
 *             -> Feedback signal instructs remote CDN to scale TCP congestion window.
 * ==============================================================================
 */

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define ETH_P_IP             0x0800
#define ETH_P_IPV6           0x86DD
#define ETH_P_NILM_PREEMPT   0x88B5  /* Custom Out-of-Band Layer-2 Energy EtherType */

#define INET_ECN_NOT_ECT     0x00
#define INET_ECN_ECT_1       0x01
#define INET_ECN_ECT_0       0x02
#define INET_ECN_CE          0x03    /* Congestion Experienced */

#define WINDOW_SIZE          8       /* 8 sequential frames sliding window */

/* Telemetry Flow Key (5-Tuple) */
struct flow_key_t {
    __u32 src_ip;
    __u32 dst_ip;
    __u16 src_port;
    __u16 dst_port;
    __u8  protocol;
    __u8  _pad[3];
};

/* Sliding Window State per 5-Tuple Flow */
struct flow_window_t {
    __u64 last_timestamp_ns;
    __u16 pkt_lengths[WINDOW_SIZE];
    __u32 delta_t_us[WINDOW_SIZE];
    __u8  head;
    __u8  count;
    __u8  active_tin;      /* 0=Bulk, 1=BestEffort, 2=Video, 3=Priority */
    __u8  inject_ecn;      /* 1 = Active TCP ECN Injection enabled */
};

/* 32-byte Zero-Copy Telemetry Metadata Record for Ring Buffer */
struct pkt_meta_t {
    __u32 src_ip;
    __u32 dst_ip;
    __u16 src_port;
    __u16 dst_port;
    __u8  protocol;
    __u8  ip_version;
    __u8  ecn_bits;         /* RFC 3168: 00=Non-ECT, 10=ECT(0), 01=ECT(1), 11=CE */
    __u8  tcp_flags;        /* SYN, ACK, FIN, RST, PSH, URG */
    __u16 pkt_len;          /* Total frame length */
    __u16 flags;            /* Bit 0: NILM Pre-empt flag, Bit 1: Retransmission */
    __u64 timestamp_ns;     /* Ingress timestamp (ktime_get_ns) */
    __u64 delta_t_ns;       /* Inter-arrival time Delta t in nanoseconds */
    __u16 len_p50;          /* 50th percentile packet length in 8-frame window */
    __u16 len_p90;          /* 90th percentile packet length in 8-frame window */
} __attribute__((packed));

/* NILM Out-of-Band Layer-2 Payload (EtherType 0x88B5) */
struct nilm_frame_t {
    __u8  magic[4];         /* "VOLT" */
    __u8  appliance_id;     /* 1=Console GPU, 2=SmartTV 4K OLED, 3=Workstation WebRTC */
    __u8  target_tin;       /* 0=Bulk, 1=BestEffort, 2=Video, 3=Priority */
    __u16 transient_ma;     /* Current transient in milli-amperes */
    __u16 lead_time_ms;     /* Pre-emption lead time (8 - 12 ms) */
    __u16 checksum;
} __attribute__((packed));

/*
 * BPF Maps Definition
 */

/* 1. Lockless Circular Ring Buffer (Supported in Linux 5.8+) */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024); /* 256 KB memory-mapped circular buffer */
} telemetry_ringbuf SEC(".maps");

/* 2. Flow Sliding Window State Hash Map (8 frames) */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 65536);
    __type(key, struct flow_key_t);
    __type(value, struct flow_window_t);
} flow_state_map SEC(".maps");

/* 3. Pre-Emptive State Array: Stores active NILM device intent registers */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 16);
    __type(key, __u32);
    __type(value, struct nilm_frame_t);
} nilm_state_table SEC(".maps");

/* 4. Active Egress ECN Injection Targets Map */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, struct flow_key_t);
    __type(value, __u8); /* 1 = Inject ECN CE bits */
} ecn_target_map SEC(".maps");


/*
 * Helper: Inline insertion sort for 8 packet length quantiles
 */
static __always_inline void calculate_quantiles(const __u16 *lengths, __u8 count, __u16 *p50, __u16 *p90) {
    __u16 sorted[WINDOW_SIZE];
    #pragma unroll
    for (int i = 0; i < WINDOW_SIZE; i++) {
        sorted[i] = lengths[i];
    }

    /* Bounded insertion sort over 8 elements */
    #pragma unroll
    for (int i = 1; i < WINDOW_SIZE; i++) {
        __u16 key = sorted[i];
        int j = i - 1;
        while (j >= 0 && sorted[j] > key) {
            sorted[j + 1] = sorted[j];
            j--;
        }
        sorted[j + 1] = key;
    }

    if (count > 0) {
        *p50 = sorted[count / 2];
        *p90 = sorted[(count * 9) / 10];
    } else {
        *p50 = 0;
        *p90 = 0;
    }
}


/* =============================================================================
 * HOOK 1: eXpress Data Path (XDP Hook) - INGRESS FAST PATH
 * ============================================================================= */
SEC("xdp")
int aegis_volt_xdp_ingress(struct xdp_md *ctx) {
    void *data_end = (void *)(long)ctx->data_end;
    void *data = (void *)(long)ctx->data;
    __u64 now_ns = bpf_ktime_get_ns();

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    __u16 h_proto = bpf_ntohs(eth->h_proto);

    /* -------------------------------------------------------------------------
     * PATH A: Out-of-Band Physical Grid NILM Pre-Emptive Frame (0x88B5)
     * ------------------------------------------------------------------------- */
    if (h_proto == ETH_P_NILM_PREEMPT) {
        struct nilm_frame_t *nilm = (void *)(eth + 1);
        if ((void *)(nilm + 1) > data_end)
            return XDP_DROP;

        if (nilm->magic[0] == 'V' && nilm->magic[1] == 'O' &&
            nilm->magic[2] == 'L' && nilm->magic[3] == 'T') {

            __u32 key = (__u32)nilm->appliance_id;
            bpf_map_update_elem(&nilm_state_table, &key, nilm, BPF_ANY);

            struct pkt_meta_t *meta = bpf_ringbuf_reserve(&telemetry_ringbuf, sizeof(*meta), 0);
            if (meta) {
                meta->src_ip = 0xFFFFFFFF;
                meta->dst_ip = 0;
                meta->src_port = nilm->transient_ma;
                meta->dst_port = nilm->lead_time_ms;
                meta->protocol = 0xFE;
                meta->ip_version = 0;
                meta->ecn_bits = nilm->target_tin;
                meta->tcp_flags = 0;
                meta->pkt_len = sizeof(struct nilm_frame_t);
                meta->flags = 0x01; /* NILM pre-emptive event */
                meta->timestamp_ns = now_ns;
                meta->delta_t_ns = 0;
                meta->len_p50 = 0;
                meta->len_p90 = 0;
                bpf_ringbuf_submit(meta, 0);
            }
        }
        /* Drop out-of-band energy frame so it does not pollute the L3 network stack */
        return XDP_DROP;
    }

    /* -------------------------------------------------------------------------
     * PATH B: Standard L3 Ingress Telemetry (IPv4 / IPv6)
     * ------------------------------------------------------------------------- */
    struct flow_key_t key;
    __builtin_memset(&key, 0, sizeof(key));

    __u8 ecn = 0;
    __u8 tcp_flags = 0;
    __u16 pkt_len = 0;
    __u8 ip_ver = 0;

    if (h_proto == ETH_P_IP) {
        struct iphdr *ip = (void *)(eth + 1);
        if ((void *)(ip + 1) > data_end)
            return XDP_PASS;

        ip_ver = 4;
        key.src_ip = ip->saddr;
        key.dst_ip = ip->daddr;
        key.protocol = ip->protocol;
        pkt_len = bpf_ntohs(ip->tot_len);
        ecn = ip->tos & 0x03;

        void *l4_hdr = (void *)ip + (ip->ihl * 4);
        if (ip->protocol == IPPROTO_TCP) {
            struct tcphdr *tcp = l4_hdr;
            if ((void *)(tcp + 1) <= data_end) {
                key.src_port = bpf_ntohs(tcp->source);
                key.dst_port = bpf_ntohs(tcp->dest);
                tcp_flags = ((__u8 *)tcp)[13];
            }
        } else if (ip->protocol == IPPROTO_UDP) {
            struct udphdr *udp = l4_hdr;
            if ((void *)(udp + 1) <= data_end) {
                key.src_port = bpf_ntohs(udp->source);
                key.dst_port = bpf_ntohs(udp->dest);
            }
        }
    } else if (h_proto == ETH_P_IPV6) {
        struct ipv6hdr *ip6 = (void *)(eth + 1);
        if ((void *)(ip6 + 1) > data_end)
            return XDP_PASS;

        ip_ver = 6;
        key.src_ip = ip6->saddr.s6_addr32[3];
        key.dst_ip = ip6->daddr.s6_addr32[3];
        key.protocol = ip6->nexthdr;
        pkt_len = bpf_ntohs(ip6->payload_len) + sizeof(struct ipv6hdr);
        ecn = (ip6->priority >> 4) & 0x03;

        void *l4_hdr = (void *)(ip6 + 1);
        if (ip6->nexthdr == IPPROTO_TCP) {
            struct tcphdr *tcp = l4_hdr;
            if ((void *)(tcp + 1) <= data_end) {
                key.src_port = bpf_ntohs(tcp->source);
                key.dst_port = bpf_ntohs(tcp->dest);
                tcp_flags = ((__u8 *)tcp)[13];
            }
        } else if (ip6->nexthdr == IPPROTO_UDP) {
            struct udphdr *udp = l4_hdr;
            if ((void *)(udp + 1) <= data_end) {
                key.src_port = bpf_ntohs(udp->source);
                key.dst_port = bpf_ntohs(udp->dest);
            }
        }
    } else {
        return XDP_PASS;
    }

    /* -------------------------------------------------------------------------
     * Sliding Window Inter-Arrival Time (Delta t) & Quantiles Calculation
     * ------------------------------------------------------------------------- */
    __u64 delta_t_ns = 0;
    __u16 p50 = pkt_len;
    __u16 p90 = pkt_len;

    struct flow_window_t *win = bpf_map_lookup_elem(&flow_state_map, &key);
    if (win) {
        if (now_ns > win->last_timestamp_ns) {
            delta_t_ns = now_ns - win->last_timestamp_ns;
        }
        win->last_timestamp_ns = now_ns;

        /* Update sliding window buffer */
        __u8 idx = win->head % WINDOW_SIZE;
        win->pkt_lengths[idx] = pkt_len;
        win->delta_t_us[idx] = (__u32)(delta_t_ns / 1000);
        win->head = (win->head + 1) % WINDOW_SIZE;
        if (win->count < WINDOW_SIZE)
            win->count++;

        calculate_quantiles(win->pkt_lengths, win->count, &p50, &p90);
    } else {
        struct flow_window_t new_win;
        __builtin_memset(&new_win, 0, sizeof(new_win));
        new_win.last_timestamp_ns = now_ns;
        new_win.pkt_lengths[0] = pkt_len;
        new_win.head = 1;
        new_win.count = 1;
        bpf_map_update_elem(&flow_state_map, &key, &new_win, BPF_ANY);
    }

    /* Stream Zero-Copy Telemetry Record via Lockless Ring Buffer */
    struct pkt_meta_t *meta = bpf_ringbuf_reserve(&telemetry_ringbuf, sizeof(*meta), 0);
    if (!meta)
        return XDP_PASS;

    meta->src_ip = key.src_ip;
    meta->dst_ip = key.dst_ip;
    meta->src_port = key.src_port;
    meta->dst_port = key.dst_port;
    meta->protocol = key.protocol;
    meta->ip_version = ip_ver;
    meta->ecn_bits = ecn;
    meta->tcp_flags = tcp_flags;
    meta->pkt_len = pkt_len;
    meta->flags = 0;
    meta->timestamp_ns = now_ns;
    meta->delta_t_ns = delta_t_ns;
    meta->len_p50 = p50;
    meta->len_p90 = p90;

    bpf_ringbuf_submit(meta, 0);

    return XDP_PASS;
}


/* =============================================================================
 * HOOK 2: eBPF TC Egress Modifier - ACTIVE TCP ECN INJECTION HOOK
 * =============================================================================
 * Intercepts outbound/forwarded packets before they leave the gateway.
 * When the analytics engine flags a bulk stream experiencing queue congestion,
 * this hook injects RFC 3168 ECN (CE = 0x03) directly into the IP TOS byte,
 * instructing the remote sender to throttle back without packet loss.
 * ============================================================================= */
SEC("tc")
int aegis_volt_tc_egress_ecn(struct __sk_buff *skb) {
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return TC_ACT_OK;

    if (bpf_ntohs(eth->h_proto) == ETH_P_IP) {
        struct iphdr *ip = (void *)(eth + 1);
        if ((void *)(ip + 1) > data_end)
            return TC_ACT_OK;

        if (ip->protocol == IPPROTO_TCP) {
            struct flow_key_t key;
            __builtin_memset(&key, 0, sizeof(key));
            key.src_ip = ip->saddr;
            key.dst_ip = ip->daddr;
            key.protocol = ip->protocol;

            void *l4_hdr = (void *)ip + (ip->ihl * 4);
            struct tcphdr *tcp = l4_hdr;
            if ((void *)(tcp + 1) <= data_end) {
                key.src_port = bpf_ntohs(tcp->source);
                key.dst_port = bpf_ntohs(tcp->dest);

                /* Query if this flow is flagged for Active ECN Injection */
                __u8 *should_inject = bpf_map_lookup_elem(&ecn_target_map, &key);
                if (should_inject && *should_inject == 1) {
                    /* Only mark if ECT-capable (RFC 3168 ECT(0)=0x02 or ECT(1)=0x01) */
                    __u8 current_ecn = ip->tos & 0x03;
                    if (current_ecn == INET_ECN_ECT_0 || current_ecn == INET_ECN_ECT_1) {
                        __u8 new_tos = (ip->tos & 0xFC) | INET_ECN_CE;
                        /* Write new TOS byte and update L3 checksum */
                        __u8 old_tos = ip->tos;
                        bpf_l3_csum_replace(skb, sizeof(struct ethhdr) + offsetof(struct iphdr, check), old_tos, new_tos, 2);
                        bpf_skb_store_bytes(skb, sizeof(struct ethhdr) + offsetof(struct iphdr, tos), &new_tos, sizeof(new_tos), 0);
                    }
                }
            }
        }
    }

    return TC_ACT_OK;
}

char _license[] SEC("license") = "GPL";
