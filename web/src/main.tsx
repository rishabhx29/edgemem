import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./styles/theme.css";
import { Shell } from "./components/Shell";
import Home from "./pages/Home";
import Demo from "./pages/Demo";
import HowItWorks from "./pages/HowItWorks";
import Evidence from "./pages/Evidence";

/**
 * Four routes, one per page, all static so the Python process can serve them as
 * files and answer the four API requests without owning any routing of its own.
 */
const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: "demo", element: <Demo /> },
      { path: "how", element: <HowItWorks /> },
      { path: "evidence", element: <Evidence /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);