module.exports = {
  apps: [
    {
      name: "tgbot",
      cwd: ".",
      script: "./run.sh",

      autorestart: true,
      watch: false,
      restart_delay: 3000,

      out_file: "./storage/logs/out.log",
      error_file: "./storage/logs/error.log",
      merge_logs: false
    }
  ]
}