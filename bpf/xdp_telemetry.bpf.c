/*
 * AURA-QoS: eBPF / XDP Zero-Copy Telemetry Kernel Hook
 * License: GPL v2
 * Intercepts raw Layer 2/3 frames at the NIC driver layer before sk_buff allocation.
 * Streams lean 32-byte metadata records to user-space via BPF_MAP_TYPE_RINGBUF.
 */

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define ETH_P_IP   0x0800
#define ETH_P_IPV6 0x86DD

/* 32-byte Zero-Copy Telemetry Metadata Record */
struct pkt_meta_t {
    __u32 src_ip;
    __u32 dst_ip;
    __u16 src_port;
    __u16 dst_port;
    __u8  protocol;
    __u8  ip_version;
    __u8  ecn_bits;
    __u8  tcp_flags;
    __u16 pkt_len;
    __u16 _pad;
    __u64 timestamp_ns;
} __attribute__((packed));

/* Lockless BPF Ring Buffer Map (Supported in Linux 5.8+) */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024); /* 256 KB memory-mapped circular buffer */
} telemetry_ringbuf SEC(".maps");

SEC("xdp")
int xdp_aura_telemetry(struct xdp_md *ctx) {
    void *data_end = (void *)(long)ctx->data_end;
    void *data = (void *)(long)ctx->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    __u16 h_proto = bpf_ntohs(eth->h_proto);

    /* Reserve space in lockless ring buffer */
    struct pkt_meta_t *meta = bpf_ringbuf_reserve(&telemetry_ringbuf, sizeof(*meta), 0);
    if (!meta)
        return XDP_PASS; /* Drop telemetry if user-space buffer full, do NOT drop packet */

    meta->timestamp_ns = bpf_ktime_get_ns();
    meta->src_port = 0;
    meta->dst_port = 0;
    meta->tcp_flags = 0;
    meta->ecn_bits = 0;
    meta->_pad = 0;

    if (h_proto == ETH_P_IP) {
        struct iphdr *ip = (void *)(eth + 1);
        if ((void *)(ip + 1) > data_end) {
            bpf_ringbuf_discard(meta, 0);
            return XDP_PASS;
        }

        meta->ip_version = 4;
        meta->src_ip = ip->saddr;
        meta->dst_ip = ip->daddr;
        meta->protocol = ip->protocol;
        meta->pkt_len = bpf_ntohs(ip->tot_len);
        meta->ecn_bits = ip->tos & 0x03; /* RFC 3168 ECN bits */

        void *l4_hdr = (void *)ip + (ip->ihl * 4);

        if (ip->protocol == IPPROTO_TCP) {
            struct tcphdr *tcp = l4_hdr;
            if ((void *)(tcp + 1) <= data_end) {
                meta->src_port = bpf_ntohs(tcp->source);
                meta->dst_port = bpf_ntohs(tcp->dest);
                meta->tcp_flags = ((__u8 *)tcp)[13]; /* TCP Control bits */
            }
        } else if (ip->protocol == IPPROTO_UDP) {
            struct udphdr *udp = l4_hdr;
            if ((void *)(udp + 1) <= data_end) {
                meta->src_port = bpf_ntohs(udp->source);
                meta->dst_port = bpf_ntohs(udp->dest);
            }
        }
    } else if (h_proto == ETH_P_IPV6) {
        struct ipv6hdr *ip6 = (void *)(eth + 1);
        if ((void *)(ip6 + 1) > data_end) {
            bpf_ringbuf_discard(meta, 0);
            return XDP_PASS;
        }

        meta->ip_version = 6;
        meta->src_ip = ip6->saddr.s6_addr32[3]; /* Fold IPv6 suffix for lean struct */
        meta->dst_ip = ip6->daddr.s6_addr32[3];
        meta->protocol = ip6->nexthdr;
        meta->pkt_len = bpf_ntohs(ip6->payload_len) + sizeof(struct ipv6hdr);
        meta->ecn_bits = (ip6->priority >> 2) & 0x03;

        void *l4_hdr = (void *)(ip6 + 1);
        if (ip6->nexthdr == IPPROTO_TCP) {
            struct tcphdr *tcp = l4_hdr;
            if ((void *)(tcp + 1) <= data_end) {
                meta->src_port = bpf_ntohs(tcp->source);
                meta->dst_port = bpf_ntohs(tcp->dest);
                meta->tcp_flags = ((__u8 *)tcp)[13];
            }
        } else if (ip6->nexthdr == IPPROTO_UDP) {
            struct udphdr *udp = l4_hdr;
            if ((void *)(udp + 1) <= data_end) {
                meta->src_port = bpf_ntohs(udp->source);
                meta->dst_port = bpf_ntohs(udp->dest);
            }
        }
    } else {
        /* Non-IP packet (ARP, etc) */
        bpf_ringbuf_discard(meta, 0);
        return XDP_PASS;
    }

    /* Submit record into lockless ring buffer - sub-microsecond user space delivery */
    bpf_ringbuf_submit(meta, 0);
    return XDP_PASS; /* Zero-copy, immediately pass packet down line */
}

char _license[] SEC("license") = "GPL";
