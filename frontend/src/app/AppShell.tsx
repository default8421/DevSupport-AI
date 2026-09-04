/**
 * @author: liuqinhe
 */
import { Outlet } from "react-router-dom";

import IconNav from "./IconNav";
import styles from "./shell.module.css";

export default function AppShell() {
  return (
    <div className={styles.shell}>
      <IconNav />
      <div className={styles.main}>
        <Outlet />
      </div>
    </div>
  );
}
