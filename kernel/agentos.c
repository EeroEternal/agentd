#include <linux/errno.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/utsname.h>

#define AGENTOS_VERSION "0.2.0"

static int agent_status_show(struct seq_file *m, void *v)
{
	seq_puts(m, "agentos_version=" AGENTOS_VERSION "\n");
	seq_printf(m, "kernel_release=%s\n", init_utsname()->release);
	seq_puts(m, "status=running\n");
	return 0;
}

static int __init agentos_init(void)
{
	if (!proc_create_single("agent_status", 0444, NULL, agent_status_show)) {
		pr_err("agentos: failed to create /proc/agent_status\n");
		return -ENOMEM;
	}
	return 0;
}
fs_initcall(agentos_init);
