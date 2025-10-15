fn main() {
    #[cfg(windows)]
    {
        use std::io::Write;
        let mut res = winres::WindowsResource::new();
        res.set_icon("../../res/icon.ico")
            .set_language(winapi::um::winnt::MAKELANGID(
                winapi::um::winnt::LANG_ENGLISH,
                winapi::um::winnt::SUBLANG_ENGLISH_US,
            ))
            .set_manifest_file("../../res/manifest.xml")
            // <<< metadatos visibles en Propiedades -> Detalles
            .set("CompanyName", "OFARCH S.A.S.")
            .set("ProductName", "OFARCHDesk")
            .set("FileDescription", "OFARCH Soporte Remoto")
            .set("OriginalFilename", "OFARCHDesk.exe");

        if let Err(e) = res.compile() {
            write!(std::io::stderr(), "{}", e).unwrap();
            std::process::exit(1);
        }
    }
}