import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { isLocale, LOCALE_COOKIE, negotiate } from "./config";

export default getRequestConfig(async () => {
  const chosen = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(chosen) ? chosen : negotiate((await headers()).get("accept-language"));
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
    timeZone: "Europe/Brussels",
  };
});
