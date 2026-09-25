#!/usr/bin/env python3
"""Generate the ath12k QCN6432 hybrid-bus support patch for the GL-BE3600 port.

Usage (run against the ath12k source with every other tree patch applied):
  python3 scripts/gl-be3600/gen-qcn6432-patch.py <patched-ath12k-src-dir> <out.patch>

The edits follow QSDK's 822-ath12k-qcn6432-bring-up.patch, mapped
onto backports-7.2-rc4's restructured ("wifi7 family") ath12k:
  - QCN6432 = user PD 2 on the IPQ5332 Q6, presented to Linux as a platform
    device with no MMIO of its own ("hybrid" bus): the register window (BAR)
    is obtained from firmware over QMI (msg 0x004C), accessed through the
    PCI-style static windows; interrupts are 14 platform MSIs from the GICv2m
    (QDSS 1 / CE 5 / DP 8).
  - Register offsets equal IPQ5332's; CE bases become absolute (windowed).
  - Legacy SMEM-507 user-PD boot info now describes every PD before the
    root PD is started.
"""
import os
import shutil
import subprocess
import sys

SRC, OUT = sys.argv[1], sys.argv[2]
A = "/tmp/qcn6432-patch/a/drivers/net/wireless/ath/ath12k"
B = "/tmp/qcn6432-patch/b/drivers/net/wireless/ath/ath12k"
for d in (A, B):
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(SRC, d, ignore=shutil.ignore_patterns("*.o", "*.cmd", "*.ko", "*.mod*", ".*"))


def edit(rel, old, new, count=1):
    p = os.path.join(B, rel)
    s = open(p).read()
    if s.count(old) != count:
        raise SystemExit("anchor mismatch in %s: %r found %d times" % (rel, old[:60], s.count(old)))
    open(p, "w").write(s.replace(old, new))


def append(rel, text):
    """Insert text before the header's closing include guard."""
    p = os.path.join(B, rel)
    s = open(p).read()
    i = s.rindex("#endif")
    open(p, "w").write(s[:i] + text.lstrip("\n") + "\n" + s[i:])


# --------------------------------------------------------------------------
# core.h: hw revision
edit("core.h",
     "\tATH12K_HW_IPQ5424_HW10,\n};",
     "\tATH12K_HW_IPQ5424_HW10,\n\tATH12K_HW_QCN6432_HW10,\n};")

# hw.h: user PD id of the QCN6432 (firmware q6_fw2, PAS PD 2)
edit("hw.h",
     "#define ATH12K_IPQ5332_USERPD_ID\t1\n",
     "#define ATH12K_IPQ5332_USERPD_ID\t1\n#define ATH12K_QCN6432_USERPD_ID\t2\n")

# --------------------------------------------------------------------------
# hif.h: static window hook
edit("hif.h",
     "\tvoid (*get_ce_msi_idx)(struct ath12k_base *ab, u32 ce_id, u32 *msi_idx);\n",
     "\tvoid (*get_ce_msi_idx)(struct ath12k_base *ab, u32 ce_id, u32 *msi_idx);\n"
     "\tvoid (*config_static_window)(struct ath12k_base *ab);\n")
append("hif.h", """
static inline void ath12k_hif_config_static_window(struct ath12k_base *ab)
{
	if (ab->hif.ops->config_static_window)
		ab->hif.ops->config_static_window(ab);
}
""")

# --------------------------------------------------------------------------
# qmi.h: service instance, device-info message
edit("qmi.h",
     "\tPAGEABLE_MEM_REGION_TYPE = 0x9,\n",
     "\tPAGEABLE_MEM_REGION_TYPE = 0x9,\n"
     "\tAFC_MEM_REGION_TYPE = 0xa,\n")
edit("qmi.h",
     "#define ATH12K_QMI_WLFW_SERVICE_INS_ID_V01_IPQ5332\t0x2\n",
     "#define ATH12K_QMI_WLFW_SERVICE_INS_ID_V01_IPQ5332\t0x2\n"
     "#define ATH12K_QMI_WLFW_SERVICE_INS_ID_V01_QCN6432\t0x60\n")
append("qmi.h", """
/* Hybrid (Q6-enumerated) devices such as QCN6432 report their register
 * window (BAR) over QMI instead of having an MMIO resource.
 */
#define QMI_WLANFW_DEVICE_INFO_REQ_V01			0x004C
#define QMI_WLANFW_DEVICE_INFO_REQ_MSG_V01_MAX_LEN	0
#define QMI_WLANFW_DEVICE_INFO_RESP_MSG_V01_MAX_LEN	32
#define ATH12K_QCN6432_DEVICE_BAR_SIZE			0x200000

struct qmi_wlanfw_device_info_req_msg_v01 {
	char placeholder;
};

struct qmi_wlanfw_device_info_resp_msg_v01 {
	struct qmi_response_type_v01 resp;
	u64 bar_addr;
	u32 bar_size;
	u8 bar_addr_valid;
	u8 bar_size_valid;
};

int ath12k_qmi_request_device_info(struct ath12k_base *ab);
""")

# --------------------------------------------------------------------------
# qmi.c: device-info request, called before board data download
edit("qmi.c",
     "#include \"debug.h\"\n",
     "#include \"debug.h\"\n#include \"hif.h\"\n")
edit("qmi.c",
     "/* clang stack usage explodes if this is inlined */\n"
     "static noinline_for_stack\n"
     "int ath12k_qmi_event_load_bdf(struct ath12k_qmi *qmi)\n{\n"
     "\tstruct ath12k_base *ab = qmi->ab;\n"
     "\tconst struct ath12k_hw_params *hw_params = ab->hw_params;\n"
     "\tint ret;\n\n",
     r'''static const struct qmi_elem_info qmi_wlanfw_device_info_req_msg_v01_ei[] = {
	{
		.data_type	= QMI_EOTI,
		.array_type	= NO_ARRAY,
		.tlv_type	= QMI_COMMON_TLV_TYPE,
	},
};

static const struct qmi_elem_info qmi_wlanfw_device_info_resp_msg_v01_ei[] = {
	{
		.data_type	= QMI_STRUCT,
		.elem_len	= 1,
		.elem_size	= sizeof(struct qmi_response_type_v01),
		.array_type	= NO_ARRAY,
		.tlv_type	= 0x02,
		.offset		= offsetof(struct qmi_wlanfw_device_info_resp_msg_v01,
					   resp),
		.ei_array	= qmi_response_type_v01_ei,
	},
	{
		.data_type	= QMI_OPT_FLAG,
		.elem_len	= 1,
		.elem_size	= sizeof(u8),
		.array_type	= NO_ARRAY,
		.tlv_type	= 0x10,
		.offset		= offsetof(struct qmi_wlanfw_device_info_resp_msg_v01,
					   bar_addr_valid),
	},
	{
		.data_type	= QMI_UNSIGNED_8_BYTE,
		.elem_len	= 1,
		.elem_size	= sizeof(u64),
		.array_type	= NO_ARRAY,
		.tlv_type	= 0x10,
		.offset		= offsetof(struct qmi_wlanfw_device_info_resp_msg_v01,
					   bar_addr),
	},
	{
		.data_type	= QMI_OPT_FLAG,
		.elem_len	= 1,
		.elem_size	= sizeof(u8),
		.array_type	= NO_ARRAY,
		.tlv_type	= 0x11,
		.offset		= offsetof(struct qmi_wlanfw_device_info_resp_msg_v01,
					   bar_size_valid),
	},
	{
		.data_type	= QMI_UNSIGNED_4_BYTE,
		.elem_len	= 1,
		.elem_size	= sizeof(u32),
		.array_type	= NO_ARRAY,
		.tlv_type	= 0x11,
		.offset		= offsetof(struct qmi_wlanfw_device_info_resp_msg_v01,
					   bar_size),
	},
	{
		.data_type	= QMI_EOTI,
		.array_type	= NO_ARRAY,
		.tlv_type	= QMI_COMMON_TLV_TYPE,
	},
};

/* Hybrid devices: ask the firmware where the device registers live and
 * map them; the static register windows are then configured.
 */
int ath12k_qmi_request_device_info(struct ath12k_base *ab)
{
	struct qmi_wlanfw_device_info_req_msg_v01 req = {};
	struct qmi_wlanfw_device_info_resp_msg_v01 resp = {};
	struct qmi_txn txn;
	int ret;

	if (ab->hw_rev != ATH12K_HW_QCN6432_HW10)
		return 0;

	/* The BAR mapping outlives a firmware restart, the target's window
	 * register does not: without re-programming it, the first CE access
	 * after recovery lands outside the static windows and the target
	 * faults (NOC error).
	 */
	if (ab->mem) {
		ath12k_hif_config_static_window(ab);
		return 0;
	}

	ret = qmi_txn_init(&ab->qmi.handle, &txn,
			   qmi_wlanfw_device_info_resp_msg_v01_ei, &resp);
	if (ret < 0)
		return ret;

	ret = qmi_send_request(&ab->qmi.handle, NULL, &txn,
			       QMI_WLANFW_DEVICE_INFO_REQ_V01,
			       QMI_WLANFW_DEVICE_INFO_REQ_MSG_V01_MAX_LEN,
			       qmi_wlanfw_device_info_req_msg_v01_ei, &req);
	if (ret < 0) {
		qmi_txn_cancel(&txn);
		ath12k_warn(ab, "failed to send device info request: %d\n", ret);
		return ret;
	}

	ret = qmi_txn_wait(&txn, msecs_to_jiffies(ATH12K_QMI_WLANFW_TIMEOUT_MS));
	if (ret < 0) {
		ath12k_warn(ab, "device info request timed out: %d\n", ret);
		return ret;
	}

	if (resp.resp.result != QMI_RESULT_SUCCESS_V01) {
		ath12k_warn(ab, "device info request failed: %d %d\n",
			    resp.resp.result, resp.resp.error);
		return -EINVAL;
	}

	if (!resp.bar_addr_valid || !resp.bar_size_valid || !resp.bar_addr ||
	    resp.bar_size != ATH12K_QCN6432_DEVICE_BAR_SIZE) {
		ath12k_warn(ab, "invalid device info: addr 0x%llx size 0x%x\n",
			    resp.bar_addr, resp.bar_size);
		return -EINVAL;
	}

	ab->mem = ioremap(resp.bar_addr, resp.bar_size);
	if (!ab->mem) {
		ath12k_warn(ab, "failed to map device BAR 0x%llx\n", resp.bar_addr);
		return -EIO;
	}
	ab->mem_len = resp.bar_size;

	ath12k_dbg(ab, ATH12K_DBG_QMI, "device BAR pa 0x%llx size 0x%x mapped\n",
		   resp.bar_addr, resp.bar_size);

	ath12k_hif_config_static_window(ab);

	return 0;
}

/* clang stack usage explodes if this is inlined */
static noinline_for_stack
int ath12k_qmi_event_load_bdf(struct ath12k_qmi *qmi)
{
	struct ath12k_base *ab = qmi->ab;
	const struct ath12k_hw_params *hw_params = ab->hw_params;
	int ret;

''')
# device info goes after the target-capability exchange (vendor order); the
# firmware answers QMI_ERR_INCOMPATIBLE_STATE (90) if asked before it.
edit("qmi.c",
     "\tret = ath12k_qmi_request_target_cap(ab);\n"
     "\tif (ret < 0) {\n"
     "\t\tath12k_warn(ab, \"qmi failed to req target capabilities:%d\\n\", ret);\n"
     "\t\treturn ret;\n"
     "\t}\n",
     "\tret = ath12k_qmi_request_target_cap(ab);\n"
     "\tif (ret < 0) {\n"
     "\t\tath12k_warn(ab, \"qmi failed to req target capabilities:%d\\n\", ret);\n"
     "\t\treturn ret;\n"
     "\t}\n"
     "\n"
     "\tret = ath12k_qmi_request_device_info(ab);\n"
     "\tif (ret < 0) {\n"
     "\t\tath12k_warn(ab, \"qmi failed to get device info: %d\\n\", ret);\n"
     "\t\treturn ret;\n"
     "\t}\n")

# qmi.c: a PD may name its own host-DDR block ("host-ddr-mem", like the PCI
# fixed-memory path); host DDR and board data then come from it instead of
# region 0 (which for a companion PD is the shared Q6 code region).
edit("qmi.c",
     "\t\tcase HOST_DDR_REGION_TYPE:\n"
     "\t\t\trmem = ath12k_core_get_reserved_mem(ab, 0);\n",
     "\t\tcase HOST_DDR_REGION_TYPE:\n"
     "\t\t\trmem = ath12k_core_get_rmem_by_name(ab, \"host-ddr-mem\");\n"
     "\t\t\tif (!rmem)\n"
     "\t\t\t\trmem = ath12k_core_get_reserved_mem(ab, 0);\n")
p = os.path.join(B, "qmi.c")
s = open(p).read()
a = s.index("\t\tcase BDF_MEM_REGION_TYPE:\n")
marker = "\t\t\t\tbdf_addr_offset = ab->hw_params->bdf_addr_offset;\n"
b = s.index(marker, a) + len(marker)
s = s[:a] + r'''		case BDF_MEM_REGION_TYPE:
			/* Board data sits at the start of the PD's host-DDR block
			 * when one is named, else at an offset into region 0.
			 */
			rmem = ath12k_core_get_rmem_by_name(ab, "host-ddr-mem");
			if (rmem) {
				bdf_addr_offset = 0;
			} else {
				rmem = ath12k_core_get_reserved_mem(ab, 0);
				if (!rmem) {
					ret = -ENODEV;
					goto out;
				}
				bdf_addr_offset = ab->hw_params->bdf_addr_offset;
			}

			of_property_read_u32(ab->dev->of_node,
					     "qcom,bdf-address-offset",
					     &bdf_addr_offset);
''' + s[b:]
open(p, "w").write(s)

# qmi.c: the QCN6432 firmware also asks for a small AFC payload buffer
# (type 0xa, 8 KiB). It is plain DMA memory, not a reserved region; give it
# one in the fixed-memory path instead of ignoring it, and free it properly.
edit("qmi.c",
     "\t\tdefault:\n"
     "\t\t\tath12k_warn(ab, \"qmi ignore invalid mem req type %u\\n\",\n",
     "\t\tcase AFC_MEM_REGION_TYPE:\n"
     "\t\t\tab->qmi.target_mem[idx].size = ab->qmi.target_mem[i].size;\n"
     "\t\t\tab->qmi.target_mem[idx].type = ab->qmi.target_mem[i].type;\n"
     "\t\t\tab->qmi.target_mem[idx].v.addr =\n"
     "\t\t\t\tdma_alloc_coherent(ab->dev, ab->qmi.target_mem[idx].size,\n"
     "\t\t\t\t\t\t   &ab->qmi.target_mem[idx].paddr,\n"
     "\t\t\t\t\t\t   GFP_KERNEL);\n"
     "\t\t\tif (!ab->qmi.target_mem[idx].v.addr) {\n"
     "\t\t\t\tret = -ENOMEM;\n"
     "\t\t\t\tgoto out;\n"
     "\t\t\t}\n"
     "\t\t\tab->qmi.target_mem[idx].prev_size = ab->qmi.target_mem[idx].size;\n"
     "\t\t\tab->qmi.target_mem[idx].prev_type = ab->qmi.target_mem[idx].type;\n"
     "\t\t\tath12k_dbg(ab, ATH12K_DBG_QMI, \"qmi afc mem %u bytes at %pad\\n\",\n"
     "\t\t\t\t   ab->qmi.target_mem[idx].size,\n"
     "\t\t\t\t   &ab->qmi.target_mem[idx].paddr);\n"
     "\t\t\tidx++;\n"
     "\t\t\tbreak;\n"
     "\t\tdefault:\n"
     "\t\t\tath12k_warn(ab, \"qmi ignore invalid mem req type %u\\n\",\n")
edit("qmi.c",
     "\t\t} else {\n"
     "\t\t\tif (test_bit(ATH12K_FLAG_FIXED_MEM_REGION, &ab->dev_flags) &&\n"
     "\t\t\t    ab->qmi.target_mem[i].v.ioaddr) {\n",
     "\t\t} else if (ab->qmi.target_mem[i].type == AFC_MEM_REGION_TYPE) {\n"
     "\t\t\tif (ab->qmi.target_mem[i].v.addr) {\n"
     "\t\t\t\tdma_free_coherent(ab->dev,\n"
     "\t\t\t\t\t\t  ab->qmi.target_mem[i].prev_size,\n"
     "\t\t\t\t\t\t  ab->qmi.target_mem[i].v.addr,\n"
     "\t\t\t\t\t\t  ab->qmi.target_mem[i].paddr);\n"
     "\t\t\t\tab->qmi.target_mem[i].v.addr = NULL;\n"
     "\t\t\t}\n"
     "\t\t} else {\n"
     "\t\t\tif (test_bit(ATH12K_FLAG_FIXED_MEM_REGION, &ab->dev_flags) &&\n"
     "\t\t\t    ab->qmi.target_mem[i].v.ioaddr) {\n")

# --------------------------------------------------------------------------
# wifi7/hal.c: version map entry (same HAL as IPQ5332, own register table)
edit("wifi7/hal.c",
     "\t[ATH12K_HW_IPQ5424_HW10] = {\n",
     "\t[ATH12K_HW_QCN6432_HW10] = {\n"
     "\t\t.hal_ops = &hal_qcn9274_ops,\n"
     "\t\t.hal_desc_sz = sizeof(struct hal_rx_desc_qcn9274_compact),\n"
     "\t\t.tcl_to_wbm_rbm_map = ath12k_hal_tcl_to_wbm_rbm_map_qcn9274,\n"
     "\t\t.hal_params = &ath12k_hw_hal_params_ipq5332,\n"
     "\t\t.hw_regs = &qcn6432_regs,\n"
     "\t},\n"
     "\t[ATH12K_HW_IPQ5424_HW10] = {\n")

# wifi7/hal_qcn9274.h: export
edit("wifi7/hal_qcn9274.h",
     "extern const struct ath12k_hw_regs ipq5332_regs;\n",
     "extern const struct ath12k_hw_regs ipq5332_regs;\n"
     "extern const struct ath12k_hw_regs qcn6432_regs;\n")

# wifi7/hal_qcn9274.c: qcn6432_regs = ipq5332_regs with absolute CE bases
p = os.path.join(B, "wifi7/hal_qcn9274.c")
s = open(p).read()
start = s.index("const struct ath12k_hw_regs ipq5332_regs = {")
end = s.index("\n};\n", start) + len("\n};\n")
block = s[start:end]
qcn = block.replace("ipq5332_regs", "qcn6432_regs")
ce_start = qcn.index("\t/* CE address */")
qcn = qcn[:ce_start] + (
    "\t/* CE address: reached through the static register window of the\n"
    "\t * QMI-provided BAR, so absolute WCSS offsets like QCN9274.\n"
    "\t */\n"
    "\t.umac_ce0_src_reg_base = 0x01b80000,\n"
    "\t.umac_ce0_dest_reg_base = 0x01b81000,\n"
    "\t.umac_ce1_src_reg_base = 0x01b82000,\n"
    "\t.umac_ce1_dest_reg_base = 0x01b83000,\n"
    "};\n")
head, tail = s[:end], s[end:]
s = (head + "\n/* QCN6432 (5 GHz companion of IPQ5332, user PD 2): same HAL register\n"
     " * layout as the IPQ5332 radio.\n */\n" + qcn + tail)
open(p, "w").write(s)

# --------------------------------------------------------------------------
# wifi7/hw.c: ring mask + hw params
p = os.path.join(B, "wifi7/hw.c")
s = open(p).read()
anchor = "static const struct ath12k_hw_ring_mask ath12k_wifi7_hw_ring_mask_ipq5332 = {"
i = s.index(anchor)
ring_mask = r'''/* QCN6432: eight DP MSI vectors, one per group. Every tx and rx ring gets a
 * group of its own: ath12k_wifi7_dp_service_srng() services only the highest
 * set bit of a group's tx/rx mask (fls()), so the "TX_RING_MASK_2 | _3" and
 * "RX_RING_MASK_2 | _3" groups of QSDK's first bring-up left tx completion
 * ring 2 and REO destination ring 2 unserviced (QSDK split them later, in
 * 957-wifi-ath12k-DP-MSI-changes-for-QCN6432). Rings of the single pdev only,
 * so one rxdma / mon-dest slot.
 */
static const struct ath12k_hw_ring_mask ath12k_wifi7_hw_ring_mask_qcn6432 = {
	.tx  = {
		ATH12K_TX_RING_MASK_0,
		ATH12K_TX_RING_MASK_1,
		ATH12K_TX_RING_MASK_2,
		ATH12K_TX_RING_MASK_3,
	},
	.rx_mon_dest = {
		0, 0,
		ATH12K_RX_MON_RING_MASK_0,
	},
	.rx = {
		0, 0, 0, 0,
		ATH12K_RX_RING_MASK_0,
		ATH12K_RX_RING_MASK_1,
		ATH12K_RX_RING_MASK_2,
		ATH12K_RX_RING_MASK_3,
	},
	.rx_err = {
		0, 0,
		ATH12K_RX_ERR_RING_MASK_0,
	},
	.rx_wbm_rel = {
		0, 0,
		ATH12K_RX_WBM_REL_RING_MASK_0,
	},
	.reo_status = {
		0, 0,
		ATH12K_REO_STATUS_RING_MASK_0,
	},
	.host2rxdma = {
		0, 0,
		ATH12K_HOST2RXDMA_RING_MASK_0,
	},
	.tx_mon_dest = {
		ATH12K_TX_MON_RING_MASK_0,
		ATH12K_TX_MON_RING_MASK_1,
	},
};

'''
s = s[:i] + ring_mask + s[i:]

# hw params: clone the ipq5332 entry
start = s.index('\t\t.name = "ipq5332 hw1.0",')
start = s.rindex("\t{\n", 0, start)
end = s.index("\n\t},\n", start) + len("\n\t},\n")
entry = s[start:end]
q = entry.replace('.name = "ipq5332 hw1.0"', '.name = "qcn6432 hw1.0"')
q = q.replace(".hw_rev = ATH12K_HW_IPQ5332_HW10", ".hw_rev = ATH12K_HW_QCN6432_HW10")
q = q.replace('.dir = "IPQ5332/hw1.0"', '.dir = "QCN6432/hw1.0"')
q = q.replace(".qmi_service_ins_id = ATH12K_QMI_WLFW_SERVICE_INS_ID_V01_IPQ5332",
              ".qmi_service_ins_id = ATH12K_QMI_WLFW_SERVICE_INS_ID_V01_QCN6432")
q = q.replace(".ring_mask = &ath12k_wifi7_hw_ring_mask_ipq5332",
              ".ring_mask = &ath12k_wifi7_hw_ring_mask_qcn6432")
q = q.replace(".ce_ie_addr = &ath12k_wifi7_ce_ie_addr_ipq5332", ".ce_ie_addr = NULL")
q = q.replace(".ce_remap = &ath12k_wifi7_ce_remap_ipq5332", ".ce_remap = NULL")
q = q.replace(".bdf_addr_offset = 0x1A00000", ".bdf_addr_offset = 0")
for needle in (".hw_rev = ATH12K_HW_QCN6432_HW10", '.dir = "QCN6432/hw1.0"',
               "SERVICE_INS_ID_V01_QCN6432", "ring_mask_qcn6432", ".ce_ie_addr = NULL",
               ".ce_remap = NULL", ".bdf_addr_offset = 0,"):
    if needle not in q:
        raise SystemExit("hw params clone missed: " + needle)
s = s[:end] + q + s[end:]
open(p, "w").write(s)

# --------------------------------------------------------------------------
# wifi7/ahb.c: match + probe
edit("wifi7/ahb.c",
     "\t{ .compatible = \"qcom,ipq5424-wifi\",\n",
     "\t{ .compatible = \"qcom,qcn6432-wifi\",\n"
     "\t  .data = (void *)ATH12K_HW_QCN6432_HW10,\n"
     "\t},\n"
     "\t{ .compatible = \"qcom,ipq5424-wifi\",\n")
edit("wifi7/ahb.c",
     "\tcase ATH12K_HW_IPQ5424_HW10:\n\t\tab_ahb->userpd_id = ATH12K_IPQ5332_USERPD_ID;\n",
     "\tcase ATH12K_HW_QCN6432_HW10:\n"
     "\t\t/* Q6-enumerated companion radio: user PD 2, registers via a\n"
     "\t\t * QMI-provided window, GIC MSIs.\n"
     "\t\t */\n"
     "\t\tab_ahb->userpd_id = ATH12K_QCN6432_USERPD_ID;\n"
     "\t\tab_ahb->scm_auth_enabled = true;\n"
     "\t\tab_ahb->hybrid = true;\n"
     "\t\tab->static_window_map = true;\n"
     "\t\tbreak;\n"
     "\tcase ATH12K_HW_IPQ5424_HW10:\n\t\tab_ahb->userpd_id = ATH12K_IPQ5332_USERPD_ID;\n")

# --------------------------------------------------------------------------
# ahb.h: hybrid state
edit("ahb.h",
     "\tbool rootpd_crashed;\n};",
     "\tbool rootpd_crashed;\n"
     "\n"
     "\t/* Hybrid bus (QCN6432): MSI-based interrupts, windowed register\n"
     "\t * access through a QMI-provided BAR.\n"
     "\t */\n"
     "\tbool hybrid;\n"
     "\tconst struct ath12k_msi_config *msi_config;\n"
     "\tu32 msi_ep_base_data;\n"
     "\tu32 msi_addr_lo;\n"
     "\tu32 msi_addr_hi;\n"
     "\tint msi_irq[ATH12K_AHB_HYBRID_MSI_VECTORS];\n"
     "\tu32 msi_data[ATH12K_AHB_HYBRID_MSI_VECTORS];\n"
     "\tspinlock_t window_lock;\n"
     "\t/* CE MSIs currently enabled, one bit per CE: keeps the reference\n"
     "\t * counted enable_irq()/disable_irq() as idempotent as the IE\n"
     "\t * register bits they stand in for.\n"
     "\t */\n"
     "\tunsigned long ce_irq_on;\n"
     "};")
edit("ahb.h",
     "#define ATH12K_USERPD_ID_MASK\t\t\tGENMASK(9, 8)\n",
     "#define ATH12K_USERPD_ID_MASK\t\t\tGENMASK(9, 8)\n"
     "#define ATH12K_AHB_HYBRID_MSI_VECTORS\t\t14\n")
edit("ahb.h",
     "#include \"core.h\"\n",
     "#include \"core.h\"\n#include \"pci.h\"\n")

# --------------------------------------------------------------------------
# ahb.c: hybrid implementation
edit("ahb.c",
     "#include <linux/soc/qcom/smem_state.h>\n",
     "#include <linux/soc/qcom/smem_state.h>\n#include <linux/msi.h>\n")

# CE irq enable/disable: MSI variant
edit("ahb.c",
     "static void ath12k_ahb_ce_irq_enable(struct ath12k_base *ab, u16 ce_id)\n{\n"
     "\tconst struct ce_attr *ce_attr;\n",
     "static void ath12k_ahb_ce_irq_enable(struct ath12k_base *ab, u16 ce_id)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n"
     "\tconst struct ce_attr *ce_attr;\n"
     "\n"
     "\t/* MSI: gate the Linux irq. enable_irq()/disable_irq() nest, and the\n"
     "\t * crash path skips the disable (ath12k_ahb_stop() under\n"
     "\t * CRASH_FLUSH) that recovery's ath12k_ahb_start() then undoes, so\n"
     "\t * only act on a real state change.\n"
     "\t */\n"
     "\tif (ab_ahb->hybrid) {\n"
     "\t\tif (!test_and_set_bit(ce_id, &ab_ahb->ce_irq_on))\n"
     "\t\t\tenable_irq(ab->irq_num[ATH12K_IRQ_CE0_OFFSET + ce_id]);\n"
     "\t\treturn;\n"
     "\t}\n")
edit("ahb.c",
     "static void ath12k_ahb_ce_irq_disable(struct ath12k_base *ab, u16 ce_id)\n{\n"
     "\tconst struct ce_attr *ce_attr;\n",
     "static void ath12k_ahb_ce_irq_disable(struct ath12k_base *ab, u16 ce_id)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n"
     "\tconst struct ce_attr *ce_attr;\n"
     "\n"
     "\tif (ab_ahb->hybrid) {\n"
     "\t\tif (test_and_clear_bit(ce_id, &ab_ahb->ce_irq_on))\n"
     "\t\t\tdisable_irq_nosync(ab->irq_num[ATH12K_IRQ_CE0_OFFSET + ce_id]);\n"
     "\t\treturn;\n"
     "\t}\n")

# DP interrupt placement. Both radios' data-path interrupts otherwise take
# the default affinity (CPU0), which at 160 MHz pins CPU0 in softirq with
# the other cores idle. Shared by the hybrid MSI path below and, through
# patch 331, the plain-AHB (IPQ5332) path.
edit("ahb.c",
     "static void ath12k_ahb_ce_irqs_enable(struct ath12k_base *ab)\n{\n",
     "/* Serve tx completion ring N on CPU N+1 and rx ring N on CPU N. ath12k\n"
     " * queues on the TCL ring of the sending CPU, so completions land off the\n"
     " * core that is queueing; rx spreads by the hardware's ring hash. Same\n"
     " * idea as the PPE's edma_irq_set_affinity(). Groups carrying neither a tx\n"
     " * nor an rx ring keep the default.\n"
     " */\n"
     "static void ath12k_ahb_dp_irq_set_affinity(struct ath12k_base *ab, int grp_id,\n"
     "\t\t\t\t\t   int irq)\n"
     "{\n"
     "\tconst struct ath12k_hw_ring_mask *ring_mask = ab->hw_params->ring_mask;\n"
     "\tint cpu;\n\n"
     "\tif (ring_mask->tx[grp_id])\n"
     "\t\tcpu = fls(ring_mask->tx[grp_id]);\t\t/* ring + 1 */\n"
     "\telse if (ring_mask->rx[grp_id])\n"
     "\t\tcpu = fls(ring_mask->rx[grp_id]) - 1;\t/* ring */\n"
     "\telse\n"
     "\t\treturn;\n\n"
     "\tcpu %= num_possible_cpus();\n"
     "\tif (irq_set_affinity(irq, cpumask_of(cpu)))\n"
     "\t\tath12k_warn(ab, \"failed to set affinity of DP irq %d to CPU %d\\n\",\n"
     "\t\t\t    irq, cpu);\n"
     "}\n\n"
     "static void ath12k_ahb_ce_irqs_enable(struct ath12k_base *ab)\n{\n")

# The device-wide CE_IRQ_ENABLED flag, as upstream pci.c keeps it: cleared
# before the CE MSIs are disabled, checked by the tasklet before it re-arms
# its irq, so a tasklet still running when the crash path quiesces the CEs
# cannot re-enable one behind it.
edit("ahb.c",
     "static void ath12k_ahb_ce_irqs_enable(struct ath12k_base *ab)\n{\n"
     "\tint i;\n\n"
     "\tfor (i = 0; i < ab->hw_params->ce_count; i++) {\n",
     "static void ath12k_ahb_ce_irqs_enable(struct ath12k_base *ab)\n{\n"
     "\tint i;\n\n"
     "\tset_bit(ATH12K_FLAG_CE_IRQ_ENABLED, &ab->dev_flags);\n\n"
     "\tfor (i = 0; i < ab->hw_params->ce_count; i++) {\n")
edit("ahb.c",
     "static void ath12k_ahb_ce_irqs_disable(struct ath12k_base *ab)\n{\n"
     "\tint i;\n\n"
     "\tfor (i = 0; i < ab->hw_params->ce_count; i++) {\n",
     "static void ath12k_ahb_ce_irqs_disable(struct ath12k_base *ab)\n{\n"
     "\tint i;\n\n"
     "\tclear_bit(ATH12K_FLAG_CE_IRQ_ENABLED, &ab->dev_flags);\n\n"
     "\tfor (i = 0; i < ab->hw_params->ce_count; i++) {\n")
edit("ahb.c",
     "\tath12k_ce_per_engine_service(ce_pipe->ab, ce_pipe->pipe_num);\n\n"
     "\tath12k_ahb_ce_irq_enable(ce_pipe->ab, ce_pipe->pipe_num);\n}\n",
     "\tath12k_ce_per_engine_service(ce_pipe->ab, ce_pipe->pipe_num);\n\n"
     "\t/* Hybrid: a quiesce (crash path) may have begun while this ran; the\n"
     "\t * MSI stays masked until ath12k_ahb_start() re-enables the set.\n"
     "\t */\n"
     "\tif (ath12k_ab_to_ahb(ce_pipe->ab)->hybrid &&\n"
     "\t    !test_bit(ATH12K_FLAG_CE_IRQ_ENABLED, &ce_pipe->ab->dev_flags))\n"
     "\t\treturn;\n\n"
     "\tath12k_ahb_ce_irq_enable(ce_pipe->ab, ce_pipe->pipe_num);\n}\n")

# QMI service instance per user PD
edit("ahb.c",
     "\tab->qmi.service_ins_id = ab->hw_params->qmi_service_ins_id;\n}\n",
     "\tab->qmi.service_ins_id = ab->hw_params->qmi_service_ins_id;\n"
     "\n"
     "\t/* Companion radios run as further user PDs on the same Q6; the\n"
     "\t * firmware offsets their QMI instance by the PD index.\n"
     "\t */\n"
     "\tif (ab_ahb->hybrid)\n"
     "\t\tab->qmi.service_ins_id += ab_ahb->userpd_id - 1;\n"
     "}\n")
edit("ahb.c",
     "static void ath12k_ahb_init_qmi_ce_config(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_qmi_ce_cfg *cfg = &ab->qmi.ce_cfg;\n",
     "static void ath12k_ahb_init_qmi_ce_config(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n"
     "\tstruct ath12k_qmi_ce_cfg *cfg = &ab->qmi.ce_cfg;\n")

# irq config dispatch
edit("ahb.c",
     "static int ath12k_ahb_config_irq(struct ath12k_base *ab)\n{\n"
     "\tint irq, irq_idx, i;\n\tint ret;\n\n",
     "static int ath12k_ahb_config_irq(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n"
     "\tint irq, irq_idx, i;\n\tint ret;\n\n"
     "\tif (ab_ahb->hybrid)\n"
     "\t\treturn ath12k_ahb_hybrid_config_irq(ab);\n\n")

# the hybrid block itself, placed before ath12k_ahb_config_irq
hybrid_code = r'''
/* ------------------------------------------------------------------------
 * Hybrid bus: QCN6432, enumerated by the Q6 and exposed to the host as a
 * platform device. Its registers are reached through a 2 MiB window whose
 * address the firmware reports over QMI; the window is split into four
 * 512 KiB static windows (window 2: CE, window 3: UMAC, window 1: other).
 * Interrupts are GIC MSIs allocated through the platform MSI domain.
 */
#define ATH12K_AHB_WINDOW_ENABLE_BIT		0x40000000
#define ATH12K_AHB_WINDOW_REG_ADDRESS		0x310c
#define ATH12K_AHB_WINDOW_VALUE_MASK		GENMASK(24, 19)
#define ATH12K_AHB_WINDOW_START			0x80000
#define ATH12K_AHB_WINDOW_RANGE_MASK		GENMASK(18, 0)
#define ATH12K_AHB_HYBRID_UMAC_BASE		0x00a00000
#define ATH12K_AHB_HYBRID_CE_BASE		0x01b80000

static const struct ath12k_msi_config ath12k_ahb_hybrid_msi_config = {
	.total_vectors = ATH12K_AHB_HYBRID_MSI_VECTORS,
	.total_users = 3,
	.users = (struct ath12k_msi_user[]) {
		{ .name = "QDSS", .num_vectors = 1, .base_vector = 0 },
		{ .name = "CE", .num_vectors = 5, .base_vector = 1 },
		{ .name = "DP", .num_vectors = 8, .base_vector = 6 },
	},
};

static void ath12k_ahb_hybrid_config_static_window(struct ath12k_base *ab)
{
	u32 umac_window, ce_window, window;

	umac_window = u32_get_bits(ATH12K_AHB_HYBRID_UMAC_BASE,
				   ATH12K_AHB_WINDOW_VALUE_MASK);
	ce_window = u32_get_bits(ATH12K_AHB_HYBRID_CE_BASE,
				 ATH12K_AHB_WINDOW_VALUE_MASK);
	window = (umac_window << 12) | (ce_window << 6);

	iowrite32(ATH12K_AHB_WINDOW_ENABLE_BIT | window,
		  ab->mem + ATH12K_AHB_WINDOW_REG_ADDRESS);
	ath12k_dbg(ab, ATH12K_DBG_AHB, "static window 0x%x reads back 0x%x\n",
		   ATH12K_AHB_WINDOW_ENABLE_BIT | window,
		   ioread32(ab->mem + ATH12K_AHB_WINDOW_REG_ADDRESS));
}

/* Window 1 is dynamic: point it at the 512 KiB block holding offset */
static void ath12k_ahb_hybrid_select_window(struct ath12k_base *ab, u32 offset)
{
	u32 window = u32_get_bits(offset, ATH12K_AHB_WINDOW_VALUE_MASK);
	u32 prev, cur;
	int retry = 0;

	prev = ioread32(ab->mem + ATH12K_AHB_WINDOW_REG_ADDRESS) & ~0x3f;
	cur = ATH12K_AHB_WINDOW_ENABLE_BIT | prev | window;
	if (cur == (ATH12K_AHB_WINDOW_ENABLE_BIT | prev |
		    (ioread32(ab->mem + ATH12K_AHB_WINDOW_REG_ADDRESS) & 0x3f)))
		return;

	iowrite32(cur, ab->mem + ATH12K_AHB_WINDOW_REG_ADDRESS);
	while (ioread32(ab->mem + ATH12K_AHB_WINDOW_REG_ADDRESS) != cur &&
	       retry++ < 10)
		udelay(10);
}

static u32 ath12k_ahb_hybrid_window_start(struct ath12k_base *ab, u32 offset)
{
	if ((offset ^ ATH12K_AHB_HYBRID_UMAC_BASE) < ATH12K_AHB_WINDOW_RANGE_MASK)
		return 3 * ATH12K_AHB_WINDOW_START;
	if ((offset ^ ATH12K_AHB_HYBRID_CE_BASE) < ATH12K_AHB_WINDOW_RANGE_MASK)
		return 2 * ATH12K_AHB_WINDOW_START;
	return ATH12K_AHB_WINDOW_START;
}

static u32 ath12k_ahb_hybrid_read32(struct ath12k_base *ab, u32 offset)
{
	struct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);
	u32 window_start, val;

	if (WARN_ON_ONCE(!ab->mem))
		return 0;

	if (offset < ATH12K_AHB_WINDOW_START)
		return ioread32(ab->mem + offset);

	window_start = ath12k_ahb_hybrid_window_start(ab, offset);
	if (window_start == ATH12K_AHB_WINDOW_START) {
		spin_lock_bh(&ab_ahb->window_lock);
		ath12k_ahb_hybrid_select_window(ab, offset);
		val = ioread32(ab->mem + window_start +
			       (offset & ATH12K_AHB_WINDOW_RANGE_MASK));
		spin_unlock_bh(&ab_ahb->window_lock);
		return val;
	}

	return ioread32(ab->mem + window_start +
			(offset & ATH12K_AHB_WINDOW_RANGE_MASK));
}

static void ath12k_ahb_hybrid_write32(struct ath12k_base *ab, u32 offset,
				      u32 value)
{
	struct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);
	u32 window_start;

	if (WARN_ON_ONCE(!ab->mem))
		return;

	if (offset < ATH12K_AHB_WINDOW_START) {
		iowrite32(value, ab->mem + offset);
		return;
	}

	window_start = ath12k_ahb_hybrid_window_start(ab, offset);
	if (window_start == ATH12K_AHB_WINDOW_START) {
		spin_lock_bh(&ab_ahb->window_lock);
		ath12k_ahb_hybrid_select_window(ab, offset);
		iowrite32(value, ab->mem + window_start +
			  (offset & ATH12K_AHB_WINDOW_RANGE_MASK));
		spin_unlock_bh(&ab_ahb->window_lock);
		return;
	}

	iowrite32(value, ab->mem + window_start +
		  (offset & ATH12K_AHB_WINDOW_RANGE_MASK));
}

static int ath12k_ahb_hybrid_get_user_msi_assignment(struct ath12k_base *ab,
						     char *user_name,
						     int *num_vectors,
						     u32 *user_base_data,
						     u32 *base_vector)
{
	struct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);
	const struct ath12k_msi_config *msi_config = ab_ahb->msi_config;
	int idx;

	if (!msi_config)
		return -EINVAL;

	for (idx = 0; idx < msi_config->total_users; idx++) {
		if (strcmp(user_name, msi_config->users[idx].name))
			continue;

		*num_vectors = msi_config->users[idx].num_vectors;
		*base_vector = msi_config->users[idx].base_vector;
		*user_base_data = *base_vector + ab_ahb->msi_ep_base_data;
		return 0;
	}

	ath12k_err(ab, "failed to find MSI assignment for %s\n", user_name);
	return -EINVAL;
}

static void ath12k_ahb_hybrid_get_msi_address(struct ath12k_base *ab,
					      u32 *msi_addr_lo, u32 *msi_addr_hi)
{
	struct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);

	*msi_addr_lo = ab_ahb->msi_addr_lo;
	*msi_addr_hi = ab_ahb->msi_addr_hi;
}

static void ath12k_ahb_hybrid_get_ce_msi_idx(struct ath12k_base *ab, u32 ce_id,
					     u32 *msi_idx)
{
	u32 i, msi_data_idx;

	for (i = 0, msi_data_idx = 0; i < ab->hw_params->ce_count; i++) {
		if (ath12k_ce_get_attr_flags(ab, i) & CE_ATTR_DIS_INTR)
			continue;

		if (ce_id == i)
			break;

		msi_data_idx++;
	}
	*msi_idx = msi_data_idx;
}

static void ath12k_ahb_hybrid_write_msi_msg(struct msi_desc *desc,
					    struct msi_msg *msg)
{
	struct device *dev = msi_desc_to_dev(desc);
	struct ath12k_base *ab = dev_get_drvdata(dev);
	struct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);

	if (desc->msi_index >= ATH12K_AHB_HYBRID_MSI_VECTORS)
		return;

	ab_ahb->msi_data[desc->msi_index] = msg->data;
	/* One doorbell for all vectors; the first vector actually
	 * requested is the first to be composed (vector 0 never is).
	 */
	if (msg->address_lo || msg->address_hi) {
		ab_ahb->msi_addr_lo = msg->address_lo;
		ab_ahb->msi_addr_hi = msg->address_hi;
	}
}

static void ath12k_ahb_hybrid_free_irqs(struct ath12k_base *ab)
{
	struct ath12k_ext_irq_grp *irq_grp;
	int i, irq_idx;

	for (i = 0; i < ab->hw_params->ce_count; i++) {
		if (ath12k_ce_get_attr_flags(ab, i) & CE_ATTR_DIS_INTR)
			continue;
		irq_idx = ATH12K_IRQ_CE0_OFFSET + i;
		if (ab->irq_num[irq_idx] > 0)
			free_irq(ab->irq_num[irq_idx], &ab->ce.ce_pipe[i]);
		ab->irq_num[irq_idx] = 0;
	}

	for (i = 0; i < ATH12K_EXT_IRQ_GRP_NUM_MAX; i++) {
		irq_grp = &ab->ext_irq_grp[i];
		if (!irq_grp->num_irq)
			continue;
		irq_idx = irq_grp->irqs[0];
		if (ab->irq_num[irq_idx] > 0)
			free_irq(ab->irq_num[irq_idx], irq_grp);
		ab->irq_num[irq_idx] = 0;
		irq_grp->num_irq = 0;
	}
}

static int ath12k_ahb_hybrid_config_irq(struct ath12k_base *ab)
{
	struct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);
	const struct ath12k_hw_ring_mask *ring_mask = ab->hw_params->ring_mask;
	struct ath12k_ext_irq_grp *irq_grp;
	struct ath12k_ce_pipe *ce_pipe;
	u32 msi_data_count, msi_data_start, msi_irq_start, msi_data_idx;
	u32 dp_count, dp_start, dp_irq_start;
	int i, irq, irq_idx, vector, ret;

	spin_lock_init(&ab_ahb->window_lock);
	ab_ahb->msi_config = &ath12k_ahb_hybrid_msi_config;

	ret = platform_device_msi_init_and_alloc_irqs(ab->dev,
						      ATH12K_AHB_HYBRID_MSI_VECTORS,
						      ath12k_ahb_hybrid_write_msi_msg);
	if (ret) {
		ath12k_err(ab, "failed to allocate %d platform MSIs: %d\n",
			   ATH12K_AHB_HYBRID_MSI_VECTORS, ret);
		return ret;
	}

	for (i = 0; i < ATH12K_AHB_HYBRID_MSI_VECTORS; i++) {
		ab_ahb->msi_irq[i] = msi_get_virq(ab->dev, i);
		if (ab_ahb->msi_irq[i] <= 0) {
			ath12k_err(ab, "no virq for MSI vector %d\n", i);
			ret = -ENODEV;
			goto err_free_msi;
		}
	}
	/* The MSI message (GIC SPI number + doorbell address) is only composed
	 * when a vector is activated, i.e. at request_irq(); the base data is
	 * derived below once the CE and DP vectors have been requested.
	 */

	ret = ath12k_ahb_hybrid_get_user_msi_assignment(ab, "CE", &msi_data_count,
							&msi_data_start,
							&msi_irq_start);
	if (ret)
		goto err_free_msi;

	/* CE interrupts, shared round-robin over the CE vectors like PCI */
	for (i = 0, msi_data_idx = 0; i < ab->hw_params->ce_count; i++) {
		if (ath12k_ce_get_attr_flags(ab, i) & CE_ATTR_DIS_INTR)
			continue;

		vector = (msi_data_idx % msi_data_count) + msi_irq_start;
		irq = ab_ahb->msi_irq[vector];
		ce_pipe = &ab->ce.ce_pipe[i];
		irq_idx = ATH12K_IRQ_CE0_OFFSET + i;

		/* Same bottom half as the wired AHB path: the CE enable/disable
		 * hooks it uses are hybrid-aware.
		 */
		tasklet_setup(&ce_pipe->intr_tq, ath12k_ahb_ce_tasklet);

		ret = request_irq(irq, ath12k_ahb_ce_interrupt_handler,
				  IRQF_SHARED, irq_name[irq_idx], ce_pipe);
		if (ret) {
			ath12k_err(ab, "failed to request CE%d MSI irq %d: %d\n",
				   i, irq, ret);
			goto err_free_msi;
		}

		ab->irq_num[irq_idx] = irq;
		msi_data_idx++;

		/* request_irq() leaves it enabled: record that, then disable */
		set_bit(i, &ab_ahb->ce_irq_on);
		ath12k_ahb_ce_irq_disable(ab, i);
	}

	ret = ath12k_ahb_hybrid_get_user_msi_assignment(ab, "DP", &dp_count,
							&dp_start, &dp_irq_start);
	if (ret)
		goto err_free_msi;

	/* DP interrupt groups, one MSI per group */
	for (i = 0; i < ATH12K_EXT_IRQ_GRP_NUM_MAX; i++) {
		irq_grp = &ab->ext_irq_grp[i];
		irq_grp->ab = ab;
		irq_grp->grp_id = i;

		irq_grp->napi_ndev = alloc_netdev_dummy(0);
		if (!irq_grp->napi_ndev) {
			ret = -ENOMEM;
			goto err_free_msi;
		}

		netif_napi_add(irq_grp->napi_ndev, &irq_grp->napi,
			       ath12k_ahb_ext_grp_napi_poll);

		if (!(ring_mask->tx[i] || ring_mask->rx[i] ||
		      ring_mask->rx_err[i] || ring_mask->rx_wbm_rel[i] ||
		      ring_mask->reo_status[i] || ring_mask->host2rxdma[i] ||
		      ring_mask->rx_mon_dest[i] || ring_mask->tx_mon_dest[i])) {
			irq_grp->num_irq = 0;
			continue;
		}

		irq_idx = ATH12K_IRQ_CE0_OFFSET + CE_COUNT_MAX + i;
		vector = (i % dp_count) + dp_irq_start;
		irq = ab_ahb->msi_irq[vector];

		irq_set_status_flags(irq, IRQ_DISABLE_UNLAZY);
		ret = request_irq(irq, ath12k_ahb_ext_interrupt_handler,
				  IRQF_SHARED, "DP_EXT_IRQ", irq_grp);
		if (ret) {
			ath12k_err(ab, "failed to request DP MSI irq %d: %d\n",
				   irq, ret);
			goto err_free_msi;
		}

		/* Record the irq only once it is really registered: the free
		 * path keys off num_irq/irq_num, so publishing them before
		 * request_irq() would make a failure here free an irq that was
		 * never taken. The CE loop above already gets this right.
		 */
		irq_grp->num_irq = 1;
		irq_grp->irqs[0] = irq_idx;
		ab->irq_num[irq_idx] = irq;
		ath12k_ahb_dp_irq_set_affinity(ab, i, irq);

		ath12k_ahb_ext_grp_disable(irq_grp);
	}

	/* GICv2m hands out one contiguous SPI block for the allocation, so
	 * data(vector) = base + vector. Derive the base from the first CE
	 * vector and check the others against it.
	 */
	if (!ab_ahb->msi_data[msi_irq_start]) {
		ath12k_err(ab, "MSI vector %u has no message after request\n",
			   msi_irq_start);
		ret = -EINVAL;
		goto err_free_msi;
	}
	ab_ahb->msi_ep_base_data = ab_ahb->msi_data[msi_irq_start] - msi_irq_start;

	if (!ab_ahb->msi_addr_lo && !ab_ahb->msi_addr_hi) {
		ath12k_err(ab, "MSI doorbell address unknown after request\n");
		ret = -EINVAL;
		goto err_free_msi;
	}

	for (i = msi_irq_start; i < ATH12K_AHB_HYBRID_MSI_VECTORS; i++) {
		if (!ab_ahb->msi_data[i])
			continue;
		if (ab_ahb->msi_data[i] != ab_ahb->msi_ep_base_data + i) {
			ath12k_err(ab, "MSI vector %d data %u not contiguous (base %u)\n",
				   i, ab_ahb->msi_data[i], ab_ahb->msi_ep_base_data);
			ret = -EINVAL;
			goto err_free_msi;
		}
	}

	ath12k_dbg(ab, ATH12K_DBG_AHB,
		   "hybrid MSI: addr 0x%x%08x SPI data base %u, %d vectors\n",
		   ab_ahb->msi_addr_hi, ab_ahb->msi_addr_lo,
		   ab_ahb->msi_ep_base_data, ATH12K_AHB_HYBRID_MSI_VECTORS);

	return 0;

err_free_msi:
	ath12k_ahb_hybrid_free_irqs(ab);
	platform_device_msi_free_irqs_all(ab->dev);
	return ret;
}

/* ath12k_core_reset() quiesces the CEs through this op on the crash path.
 * The AHB ops leave it out: there the CE interrupt enables are target
 * registers that the Q6 restart clears. The hybrid ones are host MSIs that
 * stay live across the restart, so a CE interrupt during recovery runs the
 * tasklet, which re-posts rx buffers before ath12k_core_start() has
 * re-initialised HTC; the restarted target's HTC ready then lands in them,
 * is consumed, and ath12k_htc_init() wipes it ("ctl_resp never came in").
 * Same sequence as ath12k_pci_hif_ce_irq_disable(); the tasklet honours
 * the cleared CE_IRQ_ENABLED flag, so nothing re-arms behind this.
 */
static void ath12k_ahb_hybrid_ce_irq_disable(struct ath12k_base *ab)
{
	ath12k_ahb_ce_irqs_disable(ab);
	ath12k_ahb_sync_ce_irqs(ab);
	ath12k_ahb_cancel_tasklet(ab);
}

static const struct ath12k_hif_ops ath12k_ahb_hybrid_hif_ops = {
	.start = ath12k_ahb_start,
	.stop = ath12k_ahb_stop,
	.read32 = ath12k_ahb_hybrid_read32,
	.write32 = ath12k_ahb_hybrid_write32,
	.irq_enable = ath12k_ahb_ext_irq_enable,
	.irq_disable = ath12k_ahb_ext_irq_disable,
	.ce_irq_enable = ath12k_ahb_ce_irqs_enable,
	.ce_irq_disable = ath12k_ahb_hybrid_ce_irq_disable,
	.map_service_to_pipe = ath12k_ahb_map_service_to_pipe,
	.power_up = ath12k_ahb_power_up,
	.power_down = ath12k_ahb_power_down,
	.get_user_msi_vector = ath12k_ahb_hybrid_get_user_msi_assignment,
	.get_msi_address = ath12k_ahb_hybrid_get_msi_address,
	.get_ce_msi_idx = ath12k_ahb_hybrid_get_ce_msi_idx,
	.config_static_window = ath12k_ahb_hybrid_config_static_window,
};

'''
# insert before ath12k_ahb_config_irq; map_service_to_pipe must come first
p = os.path.join(B, "ahb.c")
s = open(p).read()
anchor = "static const struct ath12k_hif_ops ath12k_ahb_hif_ops = {"
i = s.index(anchor)
s = s[:i] + hybrid_code + s[i:]
# forward declaration for the dispatch in ath12k_ahb_config_irq (defined earlier)
s = s.replace("static int ath12k_ahb_config_irq(struct ath12k_base *ab)\n{\n"
              "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n",
              "static int ath12k_ahb_hybrid_config_irq(struct ath12k_base *ab);\n\n"
              "static int ath12k_ahb_config_irq(struct ath12k_base *ab)\n{\n"
              "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n", 1)
open(p, "w").write(s)

# resource init/deinit: hybrid has no MMIO resource nor xo clock
edit("ahb.c",
     "static int ath12k_ahb_resource_init(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n"
     "\tstruct platform_device *pdev = ab->pdev;\n"
     "\tstruct resource *mem_res;\n"
     "\tint ret;\n\n",
     "static int ath12k_ahb_resource_init(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n"
     "\tstruct platform_device *pdev = ab->pdev;\n"
     "\tstruct resource *mem_res;\n"
     "\tint ret;\n\n"
     "\t/* Hybrid: the register window is learnt from firmware over QMI */\n"
     "\tif (ab_ahb->hybrid)\n"
     "\t\treturn 0;\n\n")
edit("ahb.c",
     "static void ath12k_ahb_resource_deinit(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n\n",
     "static void ath12k_ahb_resource_deinit(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_ahb *ab_ahb = ath12k_ab_to_ahb(ab);\n\n"
     "\tif (ab_ahb->hybrid) {\n"
     "\t\tif (ab->mem)\n"
     "\t\t\tiounmap(ab->mem);\n"
     "\t\tab->mem = NULL;\n"
     "\t\tath12k_ahb_hybrid_free_irqs(ab);\n"
     "\t\tplatform_device_msi_free_irqs_all(ab->dev);\n"
     "\t\treturn;\n"
     "\t}\n\n")

# probe: select hybrid hif ops after the family probe decided
edit("ahb.c",
     "\tath12k_dbg(ab, ATH12K_DBG_AHB, \"AHB device family id: %d\\n\", device_id);\n",
     "\tath12k_dbg(ab, ATH12K_DBG_AHB, \"AHB device family id: %d\\n\", device_id);\n")
edit("ahb.c",
     "\t/* Set fixed_mem_region to true for platforms that support fixed memory\n",
     "\tif (ab_ahb->hybrid)\n"
     "\t\tab->hif.ops = &ath12k_ahb_hybrid_hif_ops;\n\n"
     "\t/* Set fixed_mem_region to true for platforms that support fixed memory\n")

# --------------------------------------------------------------------------
# Legacy SMEM-507 boot info: describe every user PD (this device and its
# siblings sharing the root PD), not just the first one.
p = os.path.join(B, "ahb.c")
s = open(p).read()
fs = s.index("static int ath12k_ahb_prepare_userpd_boot_info(struct ath12k_base *ab)")
fe = s.index("\n}\n", fs) + len("\n}\n")
new_fn = r'''#define ATH12K_USERPD_BOOT_INFO_MAX_ENTRIES	3

static int ath12k_ahb_userpd_boot_info_entry(struct ath12k_base *ab,
					     struct device_node *np, u8 pid,
					     struct ath12k_userpd_boot_info_entry *entry)
{
	const struct elf32_hdr *ehdr;
	const struct firmware *fw;
	const char *fw_name;
	ssize_t image_size;
	int ret;

	ret = of_property_read_string_index(np, "firmware-name", 0, &fw_name);
	if (ret)
		return ret;

	ret = request_firmware(&fw, fw_name, ab->dev);
	if (ret)
		return ret;

	if (fw->size < sizeof(*ehdr)) {
		ret = -EINVAL;
		goto out;
	}

	ehdr = (const struct elf32_hdr *)fw->data;
	if (memcmp(ehdr->e_ident, ELFMAG, SELFMAG) ||
	    ehdr->e_ident[EI_CLASS] != ELFCLASS32) {
		ret = -EINVAL;
		goto out;
	}

	image_size = qcom_mdt_get_size(fw);
	if (image_size <= 0 || image_size > U32_MAX) {
		ret = image_size < 0 ? image_size : -EFBIG;
		goto out;
	}

	entry->header.type = ATH12K_USERPD_BOOT_INFO_TYPE;
	entry->header.length = sizeof(*entry) - sizeof(entry->header);
	entry->pid = pid;
	entry->boot_addr = cpu_to_le32(ehdr->e_entry);
	entry->data_size = cpu_to_le32(image_size);

	ath12k_dbg(ab, ATH12K_DBG_AHB,
		   "legacy userPD boot info pid %u addr 0x%x size 0x%x (%s)\n",
		   pid, ehdr->e_entry, (u32)image_size, fw_name);
	ret = 0;
out:
	release_firmware(fw);
	return ret;
}

/* Raw boot-args bytes from the root PD's DT node ("boot-args", one byte per
 * cell, e.g. <1 5 3 1 0x2f 2 0> = a type-1 record), placed ahead of the
 * user PD entries exactly as the vendor multi-PD driver does.
 */
static int ath12k_ahb_get_rootpd_boot_args(struct ath12k_base *ab, u8 *buf,
					   size_t buf_len)
{
	struct device_node *rproc_np;
	int i, count;
	u32 val;

	rproc_np = of_parse_phandle(ab->dev->of_node, "qcom,rproc", 0);
	if (!rproc_np)
		return 0;

	count = of_property_count_u32_elems(rproc_np, "boot-args");
	if (count <= 0) {
		of_node_put(rproc_np);
		return 0;
	}

	if (count > buf_len) {
		of_node_put(rproc_np);
		return -ENOSPC;
	}

	for (i = 0; i < count; i++) {
		of_property_read_u32_index(rproc_np, "boot-args", i, &val);
		buf[i] = val;
	}

	of_node_put(rproc_np);
	return count;
}

/* The root PD reads SMEM item 507 once, when it starts, so the table must
 * describe every user PD sharing this root PD - the on-SoC radio and the
 * companion radios - regardless of which device boots the root PD first.
 *
 * Layout (vendor multi-PD driver): u16 version, u16 payload length in bytes,
 * then the raw root-PD boot-args bytes, then one 11-byte entry per user PD.
 */
static int ath12k_ahb_prepare_userpd_boot_info(struct ath12k_base *ab)
{
	struct ath12k_userpd_boot_info_entry entries[ATH12K_USERPD_BOOT_INFO_MAX_ENTRIES] = {};
	u8 boot_args[64];
	struct device_node *np;
	size_t smem_size, table_size, payload;
	void *smem;
	__le16 hdr[2];
	int n = 0, nargs, ret;
	u8 pid;

	if (!of_property_read_bool(ab->dev->of_node,
				   "qcom,legacy-userpd-bootinfo"))
		return 0;

	nargs = ath12k_ahb_get_rootpd_boot_args(ab, boot_args, sizeof(boot_args));
	if (nargs < 0)
		return nargs;

	for_each_node_with_property(np, "qcom,legacy-userpd-bootinfo") {
		if (!of_device_is_available(np))
			continue;
		if (n >= ATH12K_USERPD_BOOT_INFO_MAX_ENTRIES) {
			of_node_put(np);
			return -ENOSPC;
		}

		if (of_device_is_compatible(np, "qcom,qcn6432-wifi"))
			pid = ATH12K_QCN6432_USERPD_ID + 1;
		else
			pid = ATH12K_IPQ5332_USERPD_ID + 1;

		ret = ath12k_ahb_userpd_boot_info_entry(ab, np, pid, &entries[n]);
		if (ret) {
			ath12k_warn(ab, "failed to build boot info for %pOF: %d\n",
				    np, ret);
			of_node_put(np);
			return ret;
		}
		n++;
	}

	if (!n)
		return -ENODEV;

	payload = nargs + n * sizeof(entries[0]);
	table_size = sizeof(hdr) + payload;
	hdr[0] = cpu_to_le16(ATH12K_USERPD_BOOT_INFO_VERSION);
	hdr[1] = cpu_to_le16(payload);

	ret = qcom_smem_alloc(ATH12K_USERPD_BOOT_INFO_REMOTE_PID,
			      ATH12K_USERPD_BOOT_INFO_SMEM_ID,
			      ATH12K_USERPD_BOOT_INFO_SMEM_SIZE);
	if (ret && ret != -EEXIST)
		return ret;

	smem = qcom_smem_get(ATH12K_USERPD_BOOT_INFO_REMOTE_PID,
			     ATH12K_USERPD_BOOT_INFO_SMEM_ID, &smem_size);
	if (IS_ERR(smem))
		return PTR_ERR(smem);
	if (smem_size < table_size)
		return -ENOSPC;

	memset_io(smem, 0, smem_size);
	memcpy_toio(smem, hdr, sizeof(hdr));
	if (nargs)
		memcpy_toio(smem + sizeof(hdr), boot_args, nargs);
	memcpy_toio(smem + sizeof(hdr) + nargs, entries, n * sizeof(entries[0]));

	ath12k_dbg(ab, ATH12K_DBG_AHB,
		   "legacy userPD boot info: %d boot-args bytes, %d PD entries, %zu bytes\n",
		   nargs, n, payload);

	return 0;
}
'''
s = s[:fs] + new_fn + s[fe:]
open(p, "w").write(s)

# --------------------------------------------------------------------------
# diff
os.makedirs(os.path.dirname(OUT), exist_ok=True)
res = subprocess.run(["diff", "-ruN", "a", "b"], cwd="/tmp/qcn6432-patch",
                     capture_output=True, text=True)
body = res.stdout
if not body.strip():
    raise SystemExit("empty diff")
header = ("From: GL-BE3600 port <port@local>\n"
          "Subject: wifi: ath12k: add QCN6432 hybrid-bus (user PD 2) support\n\n"
          "QCN6432 is the 5 GHz companion radio of IPQ5332. It runs as user PD 2 on\n"
          "the SoC's Q6, is enumerated by the Q6 and appears to the host as a\n"
          "platform device without MMIO: the firmware reports the 2 MiB register\n"
          "window over QMI (device info, msg 0x004C), registers are accessed via\n"
          "PCI-style static windows, and interrupts are GICv2m platform MSIs\n"
          "(QDSS 1 / CE 5 / DP 8). HAL register layout equals IPQ5332.\n\n"
          "Modelled on QSDK's 822-ath12k-qcn6432-bring-up.patch. Also make the\n"
          "legacy SMEM-507 user-PD boot info describe every user PD sharing the\n"
          "root PD.\n\n")
open(OUT, "w").write(header + body)
print("wrote", OUT, len(body.splitlines()), "lines")
